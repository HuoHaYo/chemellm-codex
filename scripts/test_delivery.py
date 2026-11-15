"""Selective delivery acceptance in isolated storage, with no optional engines."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix='chemellm-delivery-')
DATA = Path(TEMP.name)
TASK = DATA/'task'; TASK.mkdir()
os.environ.update(CHEMELLM_DATA_ROOT=str(DATA), CHEMELLM_KERNEL_DATA=str(DATA/'kernel'),
    CHEMELLM_DATA_DIR=str(DATA/'kernel'), CHEMELLM_SNAPSHOT_DIR=str(DATA/'.chem'),
    CHEMELLM_KERNEL_TOKEN='', CHEMELLM_ALLOWED_ROOTS=json.dumps([str(TASK)]))

from fastapi.testclient import TestClient
from kernel import server as k
from app import delivery

spec = importlib.util.spec_from_file_location('verify_bundle', ROOT/'scripts/verify_bundle.py')
verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(k.app)
        self.before_zips = set(k.BUNDLES_DIR.glob('*.zip'))
        self.pid = self.post('/v1/project-init', dict(name='交付测试', task_kind='research',
            requirements=[dict(code='R1', title='最终网页')],
            deliverables=[dict(key='out', title='成果', requirementCodes=['R1'], formats=['html'])]))['project']['projectId']
        self.base = '/v1/projects/'+self.pid

    def tearDown(self):
        self.client.delete(self.base); self.client.close()

    def post(self, url, body):
        r = self.client.post(url, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def register(self, files, checks=None, run='one'):
        for f in files:
            path = TASK/f['path']; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f.pop('content', f['path']), encoding='utf-8')
        return self.post(self.base+'/artifacts/register', dict(root=str(TASK), generator='test fixture',
            run_id=run, files=files, checks=checks or []))

    def zip(self, body=None):
        bundle = self.post(self.base+'/bundle', body or {})['bundle']
        path = k.BUNDLES_DIR/bundle['name']
        return path, json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))

    def test_done_without_zip_and_clean_dependency_closure(self):
        self.register([
            dict(path='web/index.html', delivery_role='deliverable', deliverable_keys=['out'],
                depends_on=['web/app.js', 'web/assets/model.glb'], content='<script src="app.js"></script>'),
            dict(path='web/app.js', delivery_role='resource', depends_on=['web/data.json']),
            dict(path='web/data.json', delivery_role='resource', content='{"display":true}'),
            dict(path='web/assets/model.glb', delivery_role='resource', depends_on=['web/data.json']),
            dict(path='unused.png', delivery_role='resource'),
            dict(path='checks.json', delivery_role='internal'),
            dict(path='run.log', delivery_role='internal'),
            dict(path='scene.blend', delivery_role='deliverable', deliverable_keys=['out']),
        ], [dict(name='model', passed=True, report_path='checks.json', deliverable_keys=['out'])])
        reviewed = self.post(self.base+'/review', {})
        self.assertEqual(reviewed['summary']['phase'], 'done'); self.assertTrue(reviewed['review']['ready'])
        self.assertEqual(set(k.BUNDLES_DIR.glob('*.zip')), self.before_zips)
        path, manifest = self.zip()
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 5)  # Four reachable files plus readable instructions.
            self.assertIn('交付说明.md', names)
            self.assertFalse(any(n.endswith(('checks.json', '.log', '.blend', 'unused.png')) for n in names))
            self.assertFalse({'manifest.json','project.json','recompute.md'} & set(names))
            prefix = next(n[:-len('index.html')] for n in names if n.endswith('/index.html'))
            self.assertIn(prefix+'app.js', names)
            self.assertIn(prefix+'data.json', names)
            self.assertIn('app.js', archive.read(prefix+'index.html').decode())
            for f in manifest['files']:
                self.assertEqual(hashlib.sha256(archive.read(f['archivePath'])).hexdigest(), f['sha256'])
        self.assertTrue(verifier.verify(path)['hashesVerified'])
        self.assertEqual(len(k.load_project(self.pid)['files']), 8)
        # Original process records remain queryable; new artifacts invalidate completed review.
        self.register([dict(path='next.html', delivery_role='deliverable', deliverable_keys=['out'])], run='two')
        self.assertNotEqual(k.load_project(self.pid)['phase'], 'done')

    def test_native_explicit_selection_and_internal_rejection(self):
        self.register([dict(path='index.html', deliverable_keys=['out']),
            dict(path='scene.blend', delivery_role='deliverable', deliverable_keys=['out']),
            dict(path='run.log', delivery_role='internal')])
        files = k.load_project(self.pid)['files']
        native = next(f['artifactId'] for f in files if f['kind']=='blend')
        path, manifest = self.zip(dict(artifact_ids=[native]))
        self.assertEqual([f['artifactId'] for f in manifest['files'] if f.get('artifactId')], [native])
        internal = next(f['artifactId'] for f in files if f['kind']=='log')
        for ids in [[], ['missing'], [internal]]:
            self.assertEqual(self.client.post(self.base+'/bundle', json=dict(artifact_ids=ids)).status_code, 409)

    def test_dependency_failures_never_leave_valid_archives(self):
        cases = [(['missing.css'], []), (['input.json'], [dict(path='input.json',delivery_role='internal')]),
                 (['a.js'], [dict(path='a.js',delivery_role='resource',depends_on=['index.html'])])]
        for index, (deps, extra) in enumerate(cases):
            self.register([dict(path='index.html',delivery_role='deliverable',deliverable_keys=['out'],depends_on=deps), *extra], run=str(index))
            self.assertEqual(self.client.post(self.base+'/bundle',json={}).status_code,409)
            self.assertFalse(self.post(self.base+'/review', {})['review']['ready'])
        self.assertEqual(set(k.BUNDLES_DIR.glob('*.zip')), self.before_zips)
        self.assertEqual(list(k.BUNDLES_DIR.glob('*.partial')), [])
        result = self.client.post(self.base+'/artifacts/register', json=dict(root=str(TASK),generator='fixture',run_id='bad',
            files=[dict(path='index.html',depends_on=['../escape.js'])]))
        self.assertEqual(result.status_code,422)

    def test_tamper_and_no_final_files(self):
        self.assertEqual(self.client.post(self.base+'/bundle',json={}).status_code,409)
        self.register([dict(path='index.html',delivery_role='deliverable',deliverable_keys=['out'])])
        f = k.load_project(self.pid)['files'][0]
        k._artifact_path(f['artifactId']).write_text('tampered')
        self.assertEqual(self.client.post(self.base+'/bundle',json={}).status_code,409)
        self.assertEqual(set(k.BUNDLES_DIR.glob('*.zip')), self.before_zips)
        self.assertEqual(list(k.BUNDLES_DIR.glob('*.partial')), [])

    def test_legacy_batch_expansion_and_current_versions(self):
        self.register([dict(path='index.html',deliverable_keys=['out']),dict(path='app.js'),
            dict(path='data.json'),dict(path='check.json'),dict(path='run.log'),dict(path='scene.blend')],
            [dict(name='model',passed=True,report_path='check.json',deliverable_keys=['out'])])
        project = k.load_project(self.pid)
        # Model a pre-role stored registration, not a new registration with explicit dependencies.
        for f in project['files']:
            f.pop('delivery_role',None); f.pop('depends_on',None)
        selected = delivery.select_files(project)
        self.assertEqual({f['name'] for f in selected}, {'index.html','app.js','data.json'})
        k.save_project(project)
        path, manifest = self.zip()
        self.assertEqual({f['name'] for f in manifest['files']}, {'index.html','app.js','data.json','交付说明.md'})
        old = deepcopy(project['files'][0]); old['artifactId']='old'
        project['files'].insert(0,old)
        self.assertNotIn('old', {f['artifactId'] for f in delivery.select_files(project)})

    def test_roles_dependencies_are_versioned_and_formal_reports_can_be_delivered(self):
        files = [dict(path='index.html',delivery_role='deliverable',deliverable_keys=['out'],depends_on=['a.js','b.js']),
                 dict(path='a.js',delivery_role='resource'),dict(path='b.js',delivery_role='resource')]
        first = self.register(deepcopy(files))
        files[0]['depends_on'].reverse()
        repeat = self.register(deepcopy(files))
        self.assertEqual(first['registration_id'],repeat['registration_id']); self.assertEqual(repeat['added'],0)
        files[0]['depends_on'] = ['a.js']
        changed = self.register(deepcopy(files))
        self.assertNotEqual(first['registration_id'],changed['registration_id'])
        self.register([dict(path='inspection.pdf',delivery_role='deliverable',deliverable_keys=['out'])],
            [dict(name='inspection',passed=True,report_path='inspection.pdf',deliverable_keys=['out'])], run='formal')
        report = k.load_project(self.pid)['files'][-1]
        path, manifest = self.zip(dict(artifact_ids=[report['artifactId']]))
        self.assertIn('inspection.pdf',{f['name'] for f in manifest['files']})

    def test_known_generators_classify_process_files(self):
        for source, names in [('kernel export_blender', ['scene-input.json','report.json','blender_worker.py','sources/input.step']),
                              ('DWSIM Automation3', ['DWSIM输入.json','DWSIM结果.json','原始流程.dwxml']),
                              ('kernel equipment_datasheet', ['设备数据表.json'])]:
            for name in names:
                self.assertEqual(delivery.file_role(dict(source=source,relative_path=name,name=Path(name).name,
                    kind=Path(name).suffix.lstrip('.'))), 'internal')
        self.assertEqual(delivery.file_role(dict(name='data.json',delivery_role='resource')), 'resource')

    def test_verifier_external_legacy_and_no_manifest(self):
        self.register([dict(path='index.html',delivery_role='deliverable',deliverable_keys=['out'])])
        path, manifest = self.zip()
        relocated = DATA/'relocated.zip'; relocated.write_bytes(path.read_bytes())
        self.assertFalse(verifier.verify(relocated)['hashesVerified'])
        self.assertTrue(verifier.verify(relocated,path.with_suffix('.manifest.json'))['hashesVerified'])
        legacy = DATA/'legacy.zip'
        with zipfile.ZipFile(legacy,'w') as z:
            z.writestr('final.txt','final')
            z.writestr('manifest.json',json.dumps(dict(files=[dict(archivePath='final.txt',sha256=hashlib.sha256(b'final').hexdigest())])))
        self.assertTrue(verifier.verify(legacy)['hashesVerified'])
        with zipfile.ZipFile(relocated,'a') as z:
            z.writestr('extra.txt','unlisted')
        with self.assertRaisesRegex(ValueError,'sha256'):
            verifier.verify(relocated,path.with_suffix('.manifest.json'))


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        TEMP.cleanup()
