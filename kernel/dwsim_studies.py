"""Persistent serial studies. No scheduler; execution starts only on start/resume."""
from contextlib import contextmanager
import csv
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from app.pricing import _atomic as atomic_file
from app.dwsim import DwsimInput
from app.dwsim_workflows import StudyInput, metric_value


class ProjectBusy(ValueError):
    pass


def _atomic(path,value):
    # Windows readers in another kernel may briefly hold the replaced file open.
    for attempt in range(6):
        try:return atomic_file(path,value)
        except PermissionError:
            if attempt==5:raise
            time.sleep(.02*(attempt+1))


@contextmanager
def project_lease(folder):
    """OS lease releases automatically after interruption; keep the stable lock file."""
    folder.mkdir(parents=True,exist_ok=True)
    handle=(folder/'.dwsim-write.lock').open('a+b')
    try:
        handle.seek(0,2)
        if not handle.tell():handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:raise ProjectBusy('项目正在执行仿真或写入，请稍后再试') from exc
        yield
    finally:handle.close()


class Studies:
    def __init__(self, project_folder, execute, source, recover, register):
        self.project_folder=project_folder;self.execute=execute;self.source=source;self.recover=recover;self.register=register
        self.lock=threading.RLock();self.active={};self.local=threading.local()

    def folder(self,project_id):return self.project_folder(project_id)/'dwsim-studies'

    def lease(self,project_id):
        # Keep an open lock outside the project so deleting the project works on Windows.
        return project_lease(self.project_folder(project_id).parent/'.write-locks'/project_id)

    @contextmanager
    def guard(self,project_id):
        if getattr(self.local,'project_id',None)==project_id:
            yield;return
        with self.lease(project_id):
            previous=getattr(self.local,'project_id',None);self.local.project_id=project_id
            try:yield
            finally:self.local.project_id=previous

    def read(self,project_id,job_id):
        path=self.folder(project_id)/job_id/'study.json'
        with self.lock:
            if not path.is_file():raise ValueError('多工况任务不存在')
            job=json.loads(path.read_text(encoding='utf-8'))
        if (path.parent/'cancel.requested').exists():job['cancel_requested']=True
        job['progress']={status:sum(c['status']==status for c in job['cases']) for status in sorted({c['status'] for c in job['cases']})}
        if job['status']=='running' and job_id not in self.active:job['note']='任务可能由其他内核运行或曾中断；resume 会检查项目锁，不会并行重跑。'
        return job

    def call(self,project_id,p:StudyInput):
        if p.action=='status':return self.read(project_id,p.job_id)
        if p.action=='start' and p.request_key:
            for path in self.folder(project_id).glob('*/study.json'):
                previous=json.loads(path.read_text(encoding='utf-8'))
                if previous['request'].get('request_key')==p.request_key:
                    if previous['request']!=p.model_dump():raise ValueError('request_key 已对应其他方案')
                    return self.read(project_id,previous['job_id'])
        if p.action=='cancel':
            with self.lock:
                job=self.read(project_id,p.job_id)
                if job['status'] in {'completed','completed_with_failures','cancelled'}:return job
                # Separate atomic flag avoids replacing state while a case is finishing.
                (self.folder(project_id)/p.job_id/'cancel.requested').touch()
                job['cancel_requested']=True
            return {**job,'note':'已请求取消；当前单次求解结束后停止，完成结果保留。'}
        lease=self.lease(project_id);lease.__enter__()
        previous_project=getattr(self.local,'project_id',None);self.local.project_id=project_id
        try:
            if p.action=='start':
                if p.request_key:
                    for path in self.folder(project_id).glob('*/study.json'):
                        previous=json.loads(path.read_text(encoding='utf-8'))
                        if previous['request'].get('request_key')==p.request_key:
                            if previous['request']!=p.model_dump():raise ValueError('request_key 已对应其他方案')
                            lease.__exit__(None,None,None);return previous
                job_id=uuid.uuid4().hex;folder=self.folder(project_id)/job_id;folder.mkdir(parents=True)
                original=self.source(p.flowsheet_path)
                baseline=folder/('baseline'+original.suffix)
                content=original.read_bytes();baseline.write_bytes(content)
                cases=[c.model_dump() for c in p.cases]
                if p.scan:
                    cases=[]
                    for i,v in enumerate(p.scan.values):
                        if p.scan.field=='reflux_ratio':
                            obj=p.scan.object_template.model_dump();obj['spec']['column']['condenser_spec']['value']=v
                        else:obj=dict(tag=p.scan.tag,**{p.scan.field:v})
                        cases.append(dict(key='point-'+str(i+1),updates=[obj],property_packages=[]))
                job=dict(job_id=job_id,project_id=project_id,status='running',request=p.model_dump(),
                         baseline=str(baseline),baseline_sha256=hashlib.sha256(content).hexdigest(),cancel_requested=False,
                         cases=[dict(**c,status='pending') for c in cases])
            else:
                job=self.read(project_id,p.job_id);job_id=p.job_id
                if job['status'] in {'completed','completed_with_failures'}:
                    lease.__exit__(None,None,None);return job
                if hashlib.sha256(Path(job['baseline']).read_bytes()).hexdigest()!=job['baseline_sha256']:
                    raise ValueError('基准模型已变化，不能续算')
                job.update(status='running',cancel_requested=False)
                (self.folder(project_id)/job_id/'cancel.requested').unlink(missing_ok=True)
                for c in job['cases']:
                    if c['status']=='running':
                        recovered=self.recover(project_id,job_id,c['key'])
                        if recovered:
                            result=self.recover(project_id,None,recovered)
                            c.update(status='completed' if result.get('validation',{}).get('passed') is True else 'failed_validation',calculation_id=recovered)
                        else:c['status']='pending'
            with self.lock:
                _atomic(self.folder(project_id)/job_id/'study.json',job)
                thread=threading.Thread(target=self.worker,args=(job,lease),daemon=True)
                self.active[job_id]=thread
                try:thread.start()
                except Exception:self.active.pop(job_id,None);raise
            return self.read(project_id,job_id)
        except Exception:
            lease.__exit__(None,None,None);raise
        finally:self.local.project_id=previous_project

    def worker(self,job,lease):
        project_id,job_id=job['project_id'],job['job_id'];self.local.project_id=project_id
        folder=self.folder(project_id)/job_id;p=StudyInput.model_validate(job['request'])
        try:
            for case in job['cases']:
                with self.lock:
                    job['cancel_requested']=(folder/'cancel.requested').exists()
                    if job['cancel_requested']:break
                    if case['status'] not in {'pending','running'}:continue
                    case['status']='running';_atomic(folder/'study.json',job)
                try:
                    if hashlib.sha256(Path(job['baseline']).read_bytes()).hexdigest()!=job['baseline_sha256']:
                        raise ValueError('固定基准模型已变化，停止任务')
                    response=self.execute(project_id,DwsimInput(action='run',flowsheet_path=job['baseline'],updates=case['updates'],
                        property_packages=case['property_packages'],validation_options=p.validation_options,timeout_seconds=p.timeout_seconds,
                        basis_keys=p.basis_keys,run_context={'study_id':job_id,'case':case['key']}))
                    cid=response['calculationId'];result=self.recover(project_id,None,cid)
                    case.update(status='completed' if result.get('validation',{}).get('passed') is True else 'failed_validation',
                                calculation_id=cid,solved=result.get('solved'),validation_passed=result.get('validation',{}).get('passed'),
                                native_model=result.get('saved_flowsheet'))
                except Exception as exc:case.update(status='failed',error=str(getattr(exc,'detail',exc))[:2400])
                with self.lock:
                    job['cancel_requested']=(folder/'cancel.requested').exists()
                    _atomic(folder/'study.json',job)
            job['status']='cancelled' if job['cancel_requested'] else 'completed_with_failures' if any(c['status'].startswith('failed') for c in job['cases']) else 'completed'
            self.export(job,p)
        except Exception as exc:job.update(status='interrupted',error=str(exc)[:2000])
        finally:
            with self.lock:_atomic(folder/'study.json',job);self.active.pop(job_id,None)
            self.local.project_id=None;lease.__exit__(None,None,None)

    def export(self,job,p):
        # Recovering a calculation after a crash also recomputes metrics from its immutable record.
        rows=[];envelope={m.key:[] for m in p.metrics}
        for case in job['cases']:
            row={'case':case['key'],'status':case['status'],'calculation_id':case.get('calculation_id'),'validation_passed':None}
            if case.get('calculation_id'):
                result=self.recover(job['project_id'],None,case['calculation_id'])
                passed=result.get('validation',{}).get('passed');row['validation_passed']=passed
                for m in p.metrics:
                    try:
                        value,_=metric_value(result,m);row[m.key]=value
                    except ValueError as exc:
                        row[m.key]=None;row[m.key+'_error']=str(exc);row['metric_error']=True
                if passed is True and not row.get('metric_error'):
                    for m in p.metrics:envelope[m.key].append(row[m.key])
            rows.append(row)
        job['comparison']=rows;job['envelope']={k:dict(min=min(v),max=max(v),valid_points=len(v)) if v else None for k,v in envelope.items()}
        if any(r.get('validation_passed') is not True or r.get('metric_error') for r in rows) and job['status']=='completed':job['status']='completed_with_failures'
        folder=self.folder(job['project_id'])/job['job_id'];files=[]
        _atomic(folder/'comparison.json',{'rows':rows,'envelope':job['envelope'],'metrics':[m.model_dump() for m in p.metrics]});files.append(folder/'comparison.json')
        with (folder/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['case','status','calculation_id','validation_passed']+[m.key for m in p.metrics],extrasaction='ignore');writer.writeheader();writer.writerows(rows)
        files.append(folder/'comparison.csv')
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        for m in p.metrics:
            fig=Figure(figsize=(7,4));FigureCanvasAgg(fig);ax=fig.add_subplot()
            values=[r.get(m.key) if r.get('validation_passed') is True else float('nan') for r in rows]
            ax.plot(range(1,len(rows)+1),[float('nan') if v is None else v for v in values],marker='o');ax.set_xlabel('case index');ax.set_ylabel(m.key+' ['+m.unit+']');fig.tight_layout()
            image=folder/(m.key+'.png');fig.savefig(image);files.append(image)
        job['artifacts']=self.register(job['project_id'],files)
