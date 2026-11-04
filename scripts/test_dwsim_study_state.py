"""Deterministic interruption/cancellation tests; no external simulator is mocked as validated."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.dwsim_studies import Studies
from app.dwsim_workflows import StudyInput


class StudyStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.baseline=self.root/'input.dwxml';self.baseline.write_text('deterministic state-machine fixture')
        self.records={};self.context={};self.calls=[];self.started=threading.Event();self.release=threading.Event()
        self.block=True;self.manager=self.make_manager()

    def tearDown(self):
        self.release.set()
        for thread in list(self.manager.active.values()):thread.join(10)
        self.temp.cleanup()

    def execute(self,pid,p):
        key=p.run_context['case'];self.calls.append(key);self.started.set()
        if self.block and not self.release.wait(10):raise RuntimeError('test did not release worker')
        cid='calculation-'+key
        self.records[cid]={'validation':{'passed':True},'streams':[{'tag':'P','temperature_k':300+len(self.calls)}]}
        self.context[(p.run_context['study_id'],key)]=cid
        return {'calculationId':cid}

    def recover(self,pid,study,key):
        return self.records[key] if study is None else self.context.get((study,key))

    def register(self,pid,files):
        return [dict(localPath=str(f),sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in files]

    def make_manager(self):
        return Studies(lambda pid:self.root/'projects'/pid,self.execute,lambda p:Path(p),self.recover,self.register)

    def start(self):
        p=StudyInput(action='start',flowsheet_path=str(self.baseline),cases=[{'key':'low'},{'key':'high'}],
                     metrics=[dict(key='T',path='/streams/@P/temperature_k',unit='K')])
        job=self.manager.call('project',p);self.assertTrue(self.started.wait(5));return job

    def finish(self,manager,job):
        for thread in list(manager.active.values()):thread.join(15);self.assertFalse(thread.is_alive())
        return manager.read('project',job['job_id'])

    def test_cancel_checkpoint_reload_resume_and_lease(self):
        job=self.start()
        with self.assertRaises(ValueError):
            with self.manager.guard('project'):pass
        cancelled=self.manager.call('project',StudyInput(action='cancel',job_id=job['job_id']))
        self.assertTrue(cancelled['cancel_requested']);self.release.set()
        job=self.finish(self.manager,job)
        self.assertEqual(job['status'],'cancelled');self.assertEqual(self.calls,['low'])
        self.assertEqual([c['status'] for c in job['cases']],['completed','pending'])
        self.manager=self.make_manager();self.block=False
        self.manager.call('project',StudyInput(action='resume',job_id=job['job_id']))
        job=self.finish(self.manager,job)
        self.assertEqual(job['status'],'completed');self.assertEqual(self.calls,['low','high'])
        self.assertEqual(job['envelope']['T']['valid_points'],2)
        for f in job['artifacts']:self.assertEqual(hashlib.sha256(Path(f['localPath']).read_bytes()).hexdigest(),f['sha256'])

    def test_crash_after_calculation_recovers_without_duplicate_execution(self):
        job=self.start();self.manager.call('project',StudyInput(action='cancel',job_id=job['job_id']))
        self.release.set();job=self.finish(self.manager,job)
        # Simulate process death after a calculation committed but before its checkpoint.
        job['status']='running';job['cases'][0]['status']='running';job['cases'][0].pop('calculation_id')
        path=self.manager.folder('project')/job['job_id']/'study.json';path.write_text(json.dumps(job),encoding='utf-8')
        self.manager=self.make_manager();self.block=False
        self.manager.call('project',StudyInput(action='resume',job_id=job['job_id']))
        job=self.finish(self.manager,job)
        self.assertEqual(self.calls,['low','high']);self.assertEqual(job['status'],'completed')
        job['status']='interrupted';path.write_text(json.dumps(job),encoding='utf-8')
        Path(job['baseline']).write_text('changed')
        with self.assertRaisesRegex(ValueError,'基准模型'):self.manager.call('project',StudyInput(action='resume',job_id=job['job_id']))


if __name__=='__main__':unittest.main(verbosity=2)
