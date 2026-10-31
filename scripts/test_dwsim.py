"""DWSIM integration checks with isolated project data and the actual local engine.

Run: python -X utf8 scripts/test_dwsim.py
Tests needing DWSIM skip explicitly if it is absent; mocks cover failure handling.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_temporary = tempfile.TemporaryDirectory(prefix='chemellm-dwsim-tests-')
os.environ.update(CHEMELLM_DATA_ROOT=_temporary.name,
                  CHEMELLM_KERNEL_DATA=str(Path(_temporary.name)/'kernel'),
                  CHEMELLM_DATA_DIR=str(Path(_temporary.name)/'kernel'),
                  CHEMELLM_SNAPSHOT_DIR=str(Path(_temporary.name)/'.chem'),
                  CHEMELLM_KERNEL_TOKEN='', CHEMELLM_PHASE_GATE='enforce')

from fastapi.testclient import TestClient  # noqa: E402
from kernel import server  # noqa: E402
from app.dwsim import DwsimInput  # noqa: E402


def mixer_model():
    return {'compounds': ['Water'], 'property_package': 'Steam Tables (IAPWS-IF97)', 'objects': [
        {'tag': 'COLD', 'type': 'MaterialStream', 'x': 50, 'y': 50, 'temperature_k': 300,
         'pressure_pa': 200000, 'mass_flow_kg_s': 2, 'mole_fractions': {'Water': 1}},
        {'tag': 'HOT', 'type': 'MaterialStream', 'x': 50, 'y': 160, 'temperature_k': 350,
         'pressure_pa': 200000, 'mass_flow_kg_s': 1, 'mole_fractions': {'Water': 1}},
        {'tag': 'MIX-101', 'type': 'Mixer', 'x': 220, 'y': 100},
        {'tag': 'PRODUCT', 'type': 'MaterialStream', 'x': 380, 'y': 100}], 'connections': [
        {'source': 'COLD', 'target': 'MIX-101'}, {'source': 'HOT', 'target': 'MIX-101', 'target_port': 1},
        {'source': 'MIX-101', 'target': 'PRODUCT'}]}


def heater_model():
    model = mixer_model()
    model['objects'] = [model['objects'][0],
        {'tag': 'HT-101', 'type': 'Heater', 'x': 220, 'y': 50,
         'parameters': {'CalcMode': 'OutletTemperature', 'OutletTemperature': 350, 'DeltaP': 10000}},
        model['objects'][-1], {'tag': 'Q-101', 'type': 'EnergyStream', 'x': 120, 'y': 180}]
    model['connections'] = [{'source': 'COLD', 'target': 'HT-101'},
                            {'source': 'HT-101', 'target': 'PRODUCT'},
                            {'source': 'Q-101', 'target': 'HT-101', 'target_port': 1}]
    return model


class DwsimTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.project_id = self.client.post('/v1/projects', json={'name': 'DWSIM 独立回归'}).json()['project']['projectId']
        self.base = '/v1/projects/'+self.project_id

    def tearDown(self):
        self.client.delete(self.base)
        self.client.close()

    def post(self, suffix, payload):
        result = self.client.post(self.base+suffix, json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def basis(self):
        self.post('/requirements', {'items': [{'code': 'R1', 'title': 'DWSIM 稳态仿真测试'}]})
        self.post('/plan', {'deliverables': [{'key': 'sim', 'title': '流程仿真', 'requirementCodes': ['R1'], 'formats': ['dwxmz', 'csv']}]})
        self.post('/objects', {'kind': 'basis', 'key': 'feed', 'quantities': [{'key': 'flow', 'value': 2,
                  'unit': 'kg/s', 'origin': 'assumption', 'conditions': 'Water, 300 K, 200000 Pa',
                  'rationale': '仅用于确定性集成验证的合成数据'}]})

    def sim(self, **request):
        return self.post('/execute', {'tool': 'dwsim', 'args': request})

    def native(self, response):
        artifact = next(item for item in response['artifacts'] if item['name'] == 'DWSIM流程.dwxmz')
        return server._artifact_path(artifact['artifactId'])

    def test_schema_stage_and_request_validation(self):
        catalog = self.client.get('/v1/tools').json()
        self.assertIn('model', catalog['schemas']['dwsim']['properties'])
        status = self.sim(action='status')
        self.assertIsNone(status['calculationId'])
        rejected = self.client.post(self.base+'/execute', json={'tool': 'dwsim', 'args': {'action': 'create', 'model': mixer_model()}})
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.client.get(self.base).json()['project']['calculations'], [])
        for edit in ('duplicate', 'composition', 'missing-feed', 'port'):
            model = mixer_model()
            if edit == 'duplicate': model['objects'][1]['tag'] = 'COLD'
            elif edit == 'composition': model['objects'][0]['mole_fractions'] = {'Water': 0.5}
            elif edit == 'missing-feed': del model['objects'][0]['pressure_pa']
            else: model['connections'][1]['target_port'] = 0
            with self.assertRaises(ValueError, msg=edit): DwsimInput(action='create', model=model)

    def test_nonconvergence_is_recorded_and_never_passes_review(self):
        self.basis()
        def failed(request, work_dir, *, save_path):
            with zipfile.ZipFile(save_path, 'w') as archive: archive.writestr('simulation.xml', '<Simulation/>')
            return {'engine': 'DWSIM', 'version': 'mock-failure', 'solved': False,
                    'error': 'synthetic nonconvergence', 'streams': [], 'solver_errors': ['not converged']}
        with patch.object(server.dwsim_bridge, 'invoke', side_effect=failed):
            result = self.sim(action='create', model=mixer_model())
        self.assertFalse(result['result']['validation']['passed'])
        complete = self.client.get(self.base+'/calculations/'+result['calculationId']).json()['result']
        self.assertEqual(complete['error'], 'synthetic nonconvergence')
        review = self.post('/review', {})['review']
        self.assertFalse(review['ready'])
        self.assertTrue(self.native(result).is_file())

    def test_timeout_and_invalid_archive_preserve_inputs(self):
        source = Path(_temporary.name)/'unsafe.dwxmz'
        with zipfile.ZipFile(source, 'w') as archive: archive.writestr('../escape.xml', '<Simulation/>')
        with self.assertRaisesRegex(ValueError, '越界'): server.dwsim_bridge.validate_flowsheet(source)
        work = Path(_temporary.name)/'timeout'
        with patch.object(server.dwsim_bridge, 'find_home', return_value=ROOT), \
                patch.object(server.dwsim_bridge.subprocess, 'run', side_effect=server.dwsim_bridge.subprocess.TimeoutExpired('runner', 5)):
            with self.assertRaisesRegex(ValueError, '已终止'):
                server.dwsim_bridge.invoke(DwsimInput(action='create', model=mixer_model(), timeout_seconds=5), work, save_path=work/'saved.dwxmz')
        self.assertTrue((work/'runner-request.json').is_file())

    @unittest.skipUnless(server.dwsim_bridge.status()['ready'], '本机没有 DWSIM 运行环境')
    def test_actual_mixer_roundtrip_changes_catalog_and_bundle(self):
        listed = self.sim(action='catalog', compound_query='water', limit=5)['result']
        self.assertTrue(any(item['name'] == 'Water' for item in listed['compounds']))
        self.assertIn('Steam Tables (IAPWS-IF97)', listed['property_packages'])
        self.basis()
        created = self.sim(action='create', model=mixer_model())
        self.assertTrue(created['result']['validation']['passed'], created)
        product = next(s for s in created['result']['streams'] if s['tag'] == 'PRODUCT')
        self.assertAlmostEqual(product['mass_flow_kg_s'], 3, places=8)
        self.assertAlmostEqual(product['temperature_k'], 316.679, delta=0.1)
        native = self.native(created)
        digest = hashlib.sha256(native.read_bytes()).hexdigest()
        checked = self.sim(action='inspect', flowsheet_path=str(native), object_tags=['MIX-101'])['result']
        self.assertEqual(checked['objects'][0]['tag'], 'MIX-101')
        self.assertTrue(checked['objects'][0]['input_ports'][0]['attached'])
        changed = self.sim(action='run', flowsheet_path=str(native), updates=[{'tag': 'HOT', 'temperature_k': 370}])
        self.assertTrue(changed['result']['validation']['passed'], changed)
        product = next(s for s in changed['result']['streams'] if s['tag'] == 'PRODUCT')
        self.assertAlmostEqual(product['temperature_k'], 323.383, delta=0.1)
        self.assertEqual(hashlib.sha256(native.read_bytes()).hexdigest(), digest)
        self.assertNotEqual(native, self.native(changed))
        self.assertEqual(changed['result']['input_source']['sha256'], digest)
        complete = self.client.get(self.base+'/calculations/'+changed['calculationId']).json()
        self.assertEqual(complete['result']['version'], created['result']['version'])
        bundle = self.post('/bundle', {})['bundle']
        with zipfile.ZipFile(io.BytesIO(self.client.get('/v1/bundles/'+bundle['name']).content)) as archive:
            for item in json.loads(archive.read('manifest.json'))['files']:
                self.assertEqual(hashlib.sha256(archive.read(item['archivePath'])).hexdigest(), item['sha256'])

    @unittest.skipUnless(server.dwsim_bridge.status()['ready'], '本机没有 DWSIM 运行环境')
    def test_actual_heater_energy_properties_and_parameter_rejection(self):
        self.basis()
        created = self.sim(action='create', model=heater_model())
        self.assertTrue(created['result']['validation']['passed'], created)
        product = next(s for s in created['result']['streams'] if s['tag'] == 'PRODUCT')
        self.assertAlmostEqual(product['temperature_k'], 350, delta=1e-5)
        self.assertAlmostEqual(product['pressure_pa'], 190000, places=4)
        self.assertAlmostEqual(created['result']['energy_streams'][0]['energy_flow_kw'], 418.19, delta=0.5)
        path = self.native(created)
        inspected = self.sim(action='inspect', flowsheet_path=str(path), object_tags=['HT-101'])['result']['objects'][0]
        self.assertIn('OutletTemperature', next(p for p in inspected['parameters'] if p['name'] == 'CalcMode')['choices'])
        prop = next(p for p in inspected['properties'] if p['id'] == 'PROP_HT_2')
        self.assertEqual(prop['unit'], 'K')
        self.assertTrue(prop['writable'])
        changed = self.sim(action='run', flowsheet_path=str(path), updates=[{'tag': 'HT-101', 'properties': {'PROP_HT_2': 360}}])
        self.assertTrue(changed['result']['validation']['passed'], changed)
        self.assertAlmostEqual(next(s for s in changed['result']['streams'] if s['tag'] == 'PRODUCT')['temperature_k'], 360, delta=1e-5)
        failed = self.client.post(self.base+'/execute', json={'tool': 'dwsim', 'args': {'action': 'run',
            'flowsheet_path': str(path), 'updates': [{'tag': 'HT-101', 'parameters': {'FakeSetting': 1}}]}})
        self.assertEqual(failed.status_code, 422)
        self.assertIn('FakeSetting', failed.text)

    @unittest.skipUnless(server.dwsim_bridge.status()['ready'], '本机没有 DWSIM 运行环境')
    def test_actual_pr_mixture_and_unsolved_native_model(self):
        self.basis()
        model = mixer_model()
        model['compounds'] = ['Ethane', 'Methane']
        model['property_package'] = 'Peng-Robinson (PR)'
        for obj in model['objects'][:2]:
            obj['mole_fractions'] = {'Methane': 0.25, 'Ethane': 0.75}
        created = self.sim(action='create', model=model)
        self.assertTrue(created['result']['validation']['passed'], created)
        product = next(s for s in created['result']['streams'] if s['tag'] == 'PRODUCT')
        self.assertAlmostEqual(product['mass_flow_kg_s'], 3, places=8)
        fractions = dict(zip(product['compounds'], product['mole_fractions']))
        self.assertAlmostEqual(fractions['Methane'], 0.25, places=8)
        self.assertAlmostEqual(fractions['Ethane'], 0.75, places=8)
        broken = mixer_model()
        broken['objects'] = broken['objects'][:3]
        broken['connections'] = broken['connections'][:2]
        failed = self.sim(action='create', model=broken)
        self.assertFalse(failed['result']['validation']['passed'])
        self.assertFalse(failed['result']['solved'])
        self.assertTrue(self.native(failed).is_file())


if __name__ == '__main__':
    try: unittest.main(verbosity=2)
    finally: _temporary.cleanup()
