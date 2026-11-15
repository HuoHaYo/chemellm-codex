"""Measure discovery/reading overhead in temporary storage; optional native pump run."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sample(function):
    values=[]
    for _ in range(5):
        started=time.perf_counter();value=function();values.append((time.perf_counter()-started)*1000)
    return value,statistics.median(values)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'output/skill-efficiency/comparison.json')
    parser.add_argument('--native',action='store_true',help='Run one real DWSIM pump model in temporary storage')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='chemellm-skill-benchmark-') as temporary:
        data=Path(temporary)
        os.environ.update(CHEMELLM_DATA_ROOT=str(data),CHEMELLM_KERNEL_DATA=str(data/'kernel'),
                          CHEMELLM_DATA_DIR=str(data/'kernel'),CHEMELLM_SNAPSHOT_DIR=str(data/'.chem'),
                          CHEMELLM_KERNEL_TOKEN='',CHEMELLM_PHASE_GATE='enforce')
        from kernel import server as k
        from fastapi.testclient import TestClient
        full,full_ms=sample(k.tools)
        summary,summary_ms=sample(lambda:k.tools(view='summary'))
        direct,direct_ms=sample(lambda:k.tools(tool='dwsim'))
        def encode(value):return json.dumps(value,ensure_ascii=False).encode()
        _,full_serial=sample(lambda:encode(full));_,small_serial=sample(lambda:encode(summary))
        skill=ROOT/'codex-plugin/skills'
        basic_bytes=sum(p.stat().st_size for p in [skill/'chemellm-dwsim/SKILL.md',skill/'chemellm-dwsim/references/basics.md'])
        baseline_file=ROOT/'output/skill-efficiency/baseline.json'
        baseline=json.loads(baseline_file.read_text(encoding='utf-8')) if baseline_file.exists() else None
        result={'catalog_full_bytes':len(encode(full)),'catalog_summary_bytes':len(encode(summary)),
                'catalog_response_reduction':1-len(encode(summary))/len(encode(full)),
                'median_of_5_ms':{'full_catalog_generation':full_ms,'summary_generation':summary_ms,
                                  'dwsim_schema_generation':direct_ms,'full_serialization':full_serial,
                                  'summary_serialization':small_serial},
                'basic_dwsim_guidance_bytes':basic_bytes,
                'baseline_basic_dwsim_guidance_bytes':baseline['basic_dwsim_read_bytes'] if baseline else None,
                'guidance_read_reduction':1-basic_bytes/baseline['basic_dwsim_read_bytes'] if baseline else None,
                'native_runner_process_ms':None,'native_solved':None,
                'timing_boundary':'Native runner duration includes process startup, model loading, solving and saving; pure solver time is not separately instrumented.'}
        with TestClient(k.app) as client:
            calls=[]
            def post(path,body):
                calls.append({'method':'POST','path':path})
                response=client.post(path,json=body)
                response.raise_for_status()
                return response.json()
            pid=post('/v1/project-init',{'name':'benchmark fixture','task_kind':'simulation'})['project']['projectId']
            response=post('/v1/projects/'+pid+'/execute',{'tool':'convert_units','args':{'value':3600,'from_unit':'kg/h','to_unit':'kg/s'}})
            result['registration_calls_before_first_recomputable_result']=sum(c['path']=='/v1/project-init' for c in calls)
            result['calls_to_first_result']=calls.copy()
            if args.native:
                if not k.dwsim_bridge.status()['ready']:
                    raise RuntimeError('Native DWSIM benchmark requested but runner not ready')
                request=json.loads((skill/'chemellm-dwsim/references/examples/pump.json').read_text(encoding='utf-8'))['request']
                timings=[];original=k.dwsim_bridge.subprocess.run
                def timed(*a,**kw):
                    started=time.perf_counter()
                    try:return original(*a,**kw)
                    finally:timings.append((time.perf_counter()-started)*1000)
                with patch.object(k.dwsim_bridge.subprocess,'run',side_effect=timed):
                    solved=post('/v1/projects/'+pid+'/execute',{'tool':'dwsim','args':request})
                result.update(native_runner_process_ms=sum(timings),native_solved=solved.get('solved'),
                              native_validation=solved.get('verification',{}).get('validation',{}).get('passed'))
                if result['native_solved'] is not True or result['native_validation'] is not True:
                    raise RuntimeError('Native benchmark did not solve and validate: '+str(result))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
