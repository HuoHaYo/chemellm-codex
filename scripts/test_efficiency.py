"""Efficient workflow acceptance, isolated from user projects and external solvers."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix='chemellm-efficiency-')
DATA = Path(TEMP.name)/'storage'
TASK = Path(TEMP.name)/'external task'
TASK.mkdir()
os.environ.update(CHEMELLM_DATA_ROOT=str(DATA), CHEMELLM_KERNEL_DATA=str(DATA/'kernel'),
    CHEMELLM_DATA_DIR=str(DATA/'kernel'), CHEMELLM_SNAPSHOT_DIR=str(DATA/'.chem'),
    CHEMELLM_KERNEL_TOKEN='', CHEMELLM_PHASE_GATE='enforce', CHEMELLM_ALLOWED_ROOTS=json.dumps([str(TASK)]))

from fastapi.testclient import TestClient
from kernel import server as k
from app import efficiency as e, project_store as store
from app.basis_selection import BasisSelectionIn, expand_request


def group(**updates):
    return dict(source_type='user', source='测试用户参数表；只用于验证来源继承。'*12,
        conditions='同一测试工况，绝压，298 K；不代表工程采用条件。'*8,
        search_summary='测试用户已直接提供适用参数，无需外部检索；未声称执行任何资料搜索。'*12,
        rationale='本批参数采用用户提供的数据；特殊来源或适用条件以逐项覆盖字段为准。'*10,
        applicability_reason='适用于固定的回归工程；不能引用为真实项目依据。'*10, **updates)


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(k.app)
        self.ids = []

    def tearDown(self):
        for ident in self.ids:
            self.client.delete('/v1/projects/'+ident)
        self.client.close()

    def post(self, path, data):
        result = self.client.post(path, json=data)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def init(self, kind='research', formats=None):
        d = dict(key='output', title='测试交付', requirementCodes=['R1'], formats=formats or [])
        result = self.post('/v1/project-init', dict(name='效率验收', task_kind=kind,
            requirements=[dict(code='R1', title='按任务交付')], deliverables=[d]))
        ident = result['project']['projectId']; self.ids.append(ident)
        return ident

    def update(self, ident, **changes):
        project = k.load_project(ident)
        return self.post('/v1/project-init', dict(project_id=ident, task_kind=project['task_kind'],
            expected_revision=project['_store_revision'], **changes))

    def register(self, ident, files, checks=None, run='r1'):
        return self.post('/v1/projects/'+ident+'/artifacts/register', dict(root=str(TASK),
            generator='acceptance fixture, not real engineering validation', run_id=run,
            files=[dict(path=f, deliverable_keys=['output']) for f in files], checks=checks or []))

    def basis(self, ident, key='feed', **updates):
        return self.post('/v1/projects/'+ident+'/basis/select', dict(kind='basis', key=key,
            expected_revision=0, reason='测试批量继承', source_groups={'user':group()},
            quantities=[dict(key='flow', value=2, unit='kg/s', source_group='user')], **updates))

    def test_trial_before_basis_and_automatic_recomputable_record(self):
        ident = self.init('simulation')
        result = self.post('/v1/projects/'+ident+'/execute', dict(tool='convert_units',
            args=dict(value=3600, from_unit='kg/h', to_unit='kg/s')))
        self.assertTrue(result['calculationId'])
        full = self.client.get('/v1/projects/'+ident+'/calculations/'+result['calculationId']).json()
        self.assertTrue(full['ok'])
        self.assertEqual(k.load_project(ident)['calculations'][0]['args']['value'], 3600)
        runs = self.client.get('/v1/projects/'+ident+'/records?section=runs').json()['items']
        self.assertEqual(runs[0]['status'],'completed')
        self.assertEqual(runs[0]['result']['calculationId'],result['calculationId'])
        self.assertEqual(runs[0]['request']['request']['args']['value'],3600)
        self.assertLess(len(json.dumps(result).encode()), 32768)
        # Only one setup call, no requirements/plan/object calls before first result.
        self.assertEqual(k.load_project(ident)['objects'], {})

    def test_group_conflicts_roles_and_supported_input_units(self):
        from app.basis_selection import apply_selection
        compact = dict(kind='basis',key='roles',expected_revision=0,reason='测试角色',
            source_groups={'a':group(assumed=True)},quantities=[
                dict(key='flow',value=2,unit='kg/s',source_group='a',critical=True),
                dict(key='solver',value=.001,unit='dimensionless',source_group='a',role='solver',critical=True),
                dict(key='length',value=3,unit='m',source_group='a',role='geometry',critical=True)])
        request, groups = expand_request(BasisSelectionIn.model_validate(compact),{})
        selected = apply_selection({},request,'test')['quantities']
        self.assertEqual([q['sensitivity_required'] for q in selected],[True,False,False])
        self.assertTrue(all(q['assumed'] for q in selected))
        with self.assertRaisesRegex(ValueError,'来源组版本'):
            expand_request(BasisSelectionIn.model_validate(compact),{'source_groups':groups})
        for value in (True,float('inf')):
            bad = deepcopy(compact);bad['quantities'][0]['value'] = value
            with self.assertRaises(ValueError):BasisSelectionIn.model_validate(bad)
        captured = e.capture_inputs(dict(model=dict(objects=[dict(tag='U',spec=dict(pressure_pa=2e5,maximum_iterations=30),
            parameters={'unknown':17})])), 'dwsim')
        self.assertEqual({q['key']:q['unit'] for q in captured}, {'U.spec.pressure_pa':'Pa',
            'U.spec.maximum_iterations':'dimensionless','U.parameters.unknown':None})
        self.assertEqual(next(q for q in captured if q['key'].endswith('maximum_iterations'))['role'],'solver')
        self.assertTrue(all(q['origin']=='unknown' for q in captured))
        self.assertEqual(e.capture_inputs({'model':'linear','x':[1,2]},'fit_data')[0]['unit'],None)

    def test_external_scene_bundle_paths_versions_and_truthful_checks(self):
        ident = self.init('scene', ['blend', 'glb', 'html'])
        (TASK/'assets').mkdir(exist_ok=True)
        (TASK/'scene.blend').write_bytes(b'BLENDER'+b'x'*(26*1024*1024))
        (TASK/'assets/model.glb').write_bytes(b'glTF-fixture')
        (TASK/'index.html').write_text('<model-viewer src="assets/model.glb"></model-viewer>', encoding='utf-8')
        (TASK/'check.json').write_text('{"fixture":true}', encoding='utf-8')
        paths = ['scene.blend', 'assets/model.glb', 'index.html', 'check.json']
        checks = [dict(name=n, passed=True, report_path='check.json', deliverable_keys=['output'])
                  for n in ['model','connections','spatial','consistency']]
        first = self.register(ident, paths, checks)
        repeat = self.register(ident, list(reversed(paths)), list(reversed(checks)))
        self.assertEqual(first['registration_id'], repeat['registration_id'])
        self.assertEqual(repeat['added'], 0)
        reviewed = self.post('/v1/projects/'+ident+'/review', {})['review']
        self.assertTrue(reviewed['ready'], reviewed)
        self.assertFalse(reviewed['external_reports_independently_verified'])
        self.assertTrue(reviewed['warnings'])
        bundled = self.post('/v1/projects/'+ident+'/bundle', {})['bundle']
        with zipfile.ZipFile(k.BUNDLES_DIR/bundled['name']) as archive:
            prefix = 'deliverables/'+first['registration_id']+'/'
            self.assertIn(prefix+'assets/model.glb', archive.namelist())
            self.assertIn('assets/model.glb', archive.read(prefix+'index.html').decode())
            for f in json.loads(archive.read('manifest.json'))['files']:
                self.assertEqual(hashlib.sha256(archive.read(f['archivePath'])).hexdigest(), f['sha256'])
        # Originals changing outside the store do not change the fixed version.
        (TASK/'index.html').write_text('new version', encoding='utf-8')
        second = self.register(ident, paths, checks)
        self.assertNotEqual(first['registration_id'], second['registration_id'])
        registered = k.load_project(ident)['files'][0]
        k._artifact_path(registered['artifactId']).write_bytes(b'tampered fixed version')
        self.assertFalse(self.post('/v1/projects/'+ident+'/review', {})['review']['ready'])
        failed = self.client.post('/v1/projects/'+ident+'/bundle', json={})
        self.assertEqual(failed.status_code, 409)
        self.assertEqual(list(k.BUNDLES_DIR.glob('*.partial')), [])

    def test_registration_boundaries_and_missing_report(self):
        ident = self.init('scene')
        base = '/v1/projects/'+ident+'/artifacts/register'
        for root, path in [(str(TASK),'../outside'), (str(TASK.parent),'outside'), (str(TASK),'C:/windows/win.ini')]:
            result = self.client.post(base, json=dict(root=root, generator='fixture', run_id='bad', files=[dict(path=path)]))
            self.assertEqual(result.status_code, 422, result.text)
        (TASK/'check.json').write_text('{}')
        self.register(ident, ['check.json'], [dict(name='model',passed=True, report_path='check.json',deliverable_keys=['output'])])
        file = k.load_project(ident)['files'][0]
        k._artifact_path(file['artifactId']).unlink()
        result = self.post('/v1/projects/'+ident+'/review', {})['review']
        self.assertTrue(any('缺失' in s for s in result['blockers']))

    def test_failed_history_replacement_and_source_group_staleness(self):
        ident = self.init('simulation')
        self.basis(ident)
        bad = self.client.post('/v1/projects/'+ident+'/execute', json=dict(tool='convert_units',
            args=dict(value=1, from_unit='kg', to_unit='s')))
        self.assertEqual(bad.status_code, 422, bad.text)
        failed_id = bad.json()['detail']['calculationId']
        good = self.post('/v1/projects/'+ident+'/execute', dict(tool='material_balance', args={},
            basis_keys=['basis/feed']))['calculationId']
        (TASK/'result.txt').write_text('fixture calculation output')
        self.register(ident, ['result.txt'])
        d = k.load_project(ident)['plan']['deliverables'][0]
        d['calculation_ids'] = [good]
        self.update(ident, deliverables=[d])
        reviewed = self.post('/v1/projects/'+ident+'/review', {})['review']
        self.assertTrue(reviewed['ready'], reviewed)
        records = self.client.get('/v1/projects/'+ident+'/records?section=calculations').json()['items']
        self.assertIn(failed_id, [r['data']['calculationId'] for r in records])
        self.post('/v1/projects/'+ident+'/basis/select', dict(kind='basis',key='groups',expected_revision=0,
            reason='来源适用背景修正',source_groups={'user':group(expected_revision=1, assumed=True)}))
        self.assertFalse(self.post('/v1/projects/'+ident+'/review', {})['review']['ready'])

    def test_design_requires_linked_basis_and_uses_one_adopted_file_version(self):
        ident=self.init('design',['txt'])
        self.basis(ident)
        path='/v1/projects/'+ident
        unrelated=self.post(path+'/execute',dict(tool='material_balance',args={}))['calculationId']
        linked=self.post(path+'/execute',dict(tool='material_balance',args={},basis_keys=['basis/feed']))['calculationId']
        (TASK/'design.txt').write_text('current fixed calculation report')
        self.register(ident,['design.txt'],run='adopted')
        chosen=k.load_project(ident)['files'][0]['artifactId']
        d=k.load_project(ident)['plan']['deliverables'][0]
        d.update(calculation_ids=[unrelated],basis_keys=['basis/feed'],artifact_ids=[chosen])
        self.update(ident,deliverables=[d])
        denied=self.post(path+'/review',{})['review']
        self.assertFalse(denied['ready']);self.assertTrue(any('未固定' in b for b in denied['blockers']))
        d['calculation_ids']=[linked]
        self.update(ident,deliverables=[d])
        self.assertTrue(self.post(path+'/review',{})['review']['ready'])
        # Updating a second source group does not invalidate an unrelated calculation.
        self.post(path+'/basis/select',dict(kind='basis',key='other',expected_revision=0,reason='无关组更新',
            source_groups={'other':group()}))
        self.assertTrue(self.post(path+'/review',{})['review']['ready'])
        # New file versions stay historical until explicitly adopted.
        (TASK/'design.txt').write_text('unadopted version')
        self.register(ident,['design.txt'],run='unadopted')
        bundled=self.post(path+'/bundle',{})['bundle']
        with zipfile.ZipFile(k.BUNDLES_DIR/bundled['name']) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual([f['artifactId'] for f in manifest['files']],[chosen])
            self.assertIn('calculation-results/'+linked+'.json',archive.namelist())
            self.assertNotIn('calculation-results/'+unrelated+'.json',archive.namelist())

    def test_2500_parameters_two_groups_and_incremental_storage(self):
        ident = self.init()
        groups = {'a':group(), 'b':group(assumed=True)}
        compact = dict(kind='basis',key='large',expected_revision=0,reason='固定2500参数验收',source_groups=groups,
            quantities=[dict(key='p'+str(i),value=i+1,unit='kg/s',source_group='a' if i%2 else 'b') for i in range(2500)])
        expanded, _ = expand_request(BasisSelectionIn.model_validate(compact), {})
        full_bytes = len(expanded.model_dump_json().encode())
        compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode())
        started = time.perf_counter()
        result = self.post('/v1/projects/'+ident+'/basis/select', compact)
        elapsed = (time.perf_counter()-started)*1000
        self.assertLess(compact_bytes/full_bytes, .2)
        self.assertLess(len(json.dumps(result).encode()), 32768)
        before = {p:(p.stat().st_mtime_ns,p.read_bytes()) for p in (DATA/'.chem'/ident/'objects').glob('*.json')}
        self.post('/v1/projects/'+ident+'/objects', dict(kind='basis',key='other',quantities=[]))
        self.assertTrue(all((p.stat().st_mtime_ns,p.read_bytes())==v for p,v in before.items()))
        with patch.object(e, 'file_digest', side_effect=AssertionError('summary must not hash files')):
            self.assertLess(len(self.client.get('/v1/projects/'+ident).content), 32768)
        # Repeated descriptions exist once in SQLite and once in incremental text snapshots.
        repeated = groups['a']['search_summary']
        with k.connection() as db:
            raw = '\n'.join(r[0] for r in db.execute('SELECT data FROM codex_project_records WHERE project_id=?',(ident,)))
            shared = '\n'.join(r[0] for r in db.execute('SELECT value FROM codex_project_texts'))
            stored = db.execute('SELECT data FROM codex_project_records WHERE project_id=? AND section=? AND key=?',
                (ident,'objects','basis/large')).fetchone()[0]
        expanded_object=store.dumps(k.load_project(ident)['objects']['basis/large'])
        shared_values={}
        for g in groups.values():
            for text in g.values():
                if isinstance(text,str) and len(text.encode())>=96:
                    shared_values[hashlib.sha256(text.encode()).hexdigest()]=text
        storage_bytes=len(stored.encode())+sum(len(v.encode())+len(d) for d,v in shared_values.items())
        storage_reduction=1-storage_bytes/len(expanded_object.encode())
        self.assertGreater(storage_reduction,.8)
        self.assertNotIn(repeated, raw)
        self.assertEqual(shared.count(repeated), 1)
        snapshot = '\n'.join(p.read_text(encoding='utf-8') for p in (DATA/'.chem'/ident).rglob('*.json'))
        self.assertEqual(snapshot.count(repeated), 1)
        page = self.client.get('/v1/projects/'+ident+'/records?section=objects&limit=1').json()
        self.assertEqual(page['total'], 2);self.assertEqual(page['next_offset'], 1)
        timing = [json.loads(line) for line in (k.KERNEL_DATA/'metrics/efficiency.jsonl').read_text(encoding='utf-8').splitlines()]
        storage_timing = [m for m in timing if m['stage']=='storage' and m['changed_records']==3][-1]
        print(json.dumps(dict(benchmark='2500 parameters / 2 source groups',
            full_request_bytes=full_bytes, compact_request_bytes=compact_bytes,
            request_reduction=1-compact_bytes/full_bytes, repeated_text_storage_reduction=1-2/2500,
            object_logical_storage_bytes=storage_bytes, expanded_object_bytes=len(expanded_object.encode()),
            object_storage_reduction=storage_reduction,
            basis_ms=round(elapsed,3),storage_metrics=storage_timing,
            summary_bytes=len(self.client.get('/v1/projects/'+ident).content)), ensure_ascii=False))

    def test_explicit_migration_identity_conflict_and_rollback(self):
        created = self.post('/v1/projects',dict(name='legacy',workflow='legacy'))
        ident = created['project']['projectId'];self.ids.append(ident)
        path='/v1/projects/'+ident
        self.post(path+'/requirements',dict(items=[dict(code='R1',title='迁移完整关系')]))
        self.post(path+'/plan',dict(deliverables=[dict(key='report',title='报告',requirementCodes=['R1'],formats=['md'])]))
        self.post(path+'/objects', dict(kind='basis',key='old',quantities=[dict(key='flow',value=1,unit='kg/s',
            origin='assumption',conditions='合成测试',rationale='仅验证迁移，不是工程数据')]))
        self.post(path+'/execute',dict(tool='material_balance',args={}))
        self.post(path+'/execute',dict(tool='export_report',args=dict(title='迁移报告',body='fixture',formats=['md'])))
        ref=self.post('/v1/references/import',dict(text='测试迁移：保留原件版本及项目引用关联。',source=dict(title='迁移资料',source='合成原文')))['reference']
        block=self.client.get('/v1/references/'+ref['reference_id']).json()['reference']['items'][0]
        self.post(path+'/references',dict(reference_id=ref['reference_id'],revision=ref['revision'],
            block_ids=[block['id']],purpose='迁移固定引用'))
        original = k.load_project(ident)
        self.assertNotIn('workflow',self.client.get('/v1/projects/'+ident).json()['project'].get('review',{}))
        refused = self.client.post('/v1/project-init',json=dict(project_id=ident))
        self.assertEqual(refused.status_code,409)
        result = self.post('/v1/project-init',dict(project_id=ident,upgrade=True))
        self.assertTrue(result['upgraded']);self.assertTrue(Path(result['backup']).is_file())
        for section in ('objects','calculations','files','references'):
            self.assertEqual(k.load_project(ident)[section],original[section])
        stale = self.client.post('/v1/project-init',json=dict(project_id=ident,expected_revision=0))
        self.assertEqual(stale.status_code,409)
        old = self.post('/v1/projects',dict(name='rollback',workflow='legacy'))['project']['projectId'];self.ids.append(old)
        before = k.project_file(old).read_bytes()
        load = store.load
        def corrupt(ident):
            value = load(ident);value['files'].append({'fixture':'migration mismatch'});return value
        with patch.object(store,'load',side_effect=corrupt):
            with self.assertRaisesRegex(ValueError,'一致性'):
                self.client.post('/v1/project-init',json=dict(project_id=old,upgrade=True))
        self.assertFalse(store.exists(old));self.assertEqual(k.project_file(old).read_bytes(),before)
        self.assertEqual(k.load_project(old)['workflow'],'legacy')

    def test_image_pin_region_hash_and_no_exact_quote(self):
        from PIL import Image
        image = TASK/'figure.png'
        Image.new('RGB',(80,60),'white').save(image)
        k.REFERENCE_LIBRARY.allowed = [k.ROOT, DATA, TASK]
        ref = self.post('/v1/references/import',dict(path=str(image),source=dict(title='图像定位测试',source='合成图像')))
        item = ref['reference']
        ident = self.init()
        used = self.post('/v1/projects/'+ident+'/references',dict(reference_id=item['reference_id'],revision=item['revision'],
            purpose='只引用图形，不声称逐字引文',images=[dict(page=1,region=[.1,.2,.8,.9])]))
        citation = used['citations'][0]
        self.assertEqual(citation['kind'],'visual_reference');self.assertFalse(citation['exact_text_available'])
        self.assertEqual(citation['locator']['region'],[.1,.2,.8,.9])
        self.assertEqual(len(citation['image_sha256']),64)
        # The evidence engine cannot match a quotation against a visual-only block.
        from app.evidence import save_claim, add_evidence, ClaimInput, EvidenceInput
        claim=save_claim(ident,ClaimInput(text='图像引用不能作为精确文字引句'))
        with self.assertRaisesRegex(ValueError,'引句未出现'):
            add_evidence(ident,EvidenceInput(claim_id=claim['id'],expected_revision=claim['revision'],
                snapshot_id=used['snapshot_id'],block_id=citation['block_id'],quote='a precise invented quotation'))


if __name__ == '__main__':
    try:unittest.main(verbosity=2)
    finally:TEMP.cleanup()
