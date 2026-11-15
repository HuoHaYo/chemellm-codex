"""Wiring invariants, native serialization and delivery in isolated project data."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix='chemellm-qet-tests-')
STORE = Path(TEMP.name)
os.environ.update(CHEMELLM_DATA_ROOT=str(STORE), CHEMELLM_KERNEL_DATA=str(STORE/'kernel'),
    CHEMELLM_DATA_DIR=str(STORE/'kernel'), CHEMELLM_SNAPSHOT_DIR=str(STORE/'.chem'),
    CHEMELLM_KERNEL_TOKEN='', CHEMELLM_PHASE_GATE='enforce')
from fastapi.testclient import TestClient
from app.qelectrotech import QElectroTechInput
from app.external_tools import qelectrotech_status
from kernel import server as k, qelectrotech_bridge as bridge
from app import delivery


def example():
    return json.loads((ROOT/'docs/examples/qelectrotech-loop.json').read_text(encoding='utf-8'))


class QElectroTechTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(k.app)
        self.pid = self.client.post('/v1/project-init', json={'name': '独立接线验收', 'task_kind': 'research'}).json()['project']['projectId']
        self.base = '/v1/projects/'+self.pid

    def tearDown(self):
        self.client.close()

    def execute(self, args):
        return self.client.post(self.base+'/execute', json={'tool': 'qelectrotech', 'args': args})

    def test_installed_native_version(self):
        status = qelectrotech_status()
        self.assertTrue(status['ready'], status)
        self.assertTrue(status['version'].startswith('0.100'))
        self.assertFalse(status['batch_export'])
        self.assertIn('qelectrotech_wiring', self.client.get('/v1/health').json()['capabilities'])
        import subprocess
        from app.external_tools import _qet_version
        # A second QET instance may forward --version to its open GUI and emit
        # nothing; the pinned official executable remains detectable.
        with patch('app.external_tools.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            self.assertEqual(_qet_version(status['path'], -1), '0.100.0')

    def test_schema_direct_and_invariant_errors(self):
        schema = self.client.get('/v1/tools?tool=qelectrotech').json()['schema']
        self.assertIn('folios', schema['properties'])
        self.assertIn('qelectrotech', self.client.get('/v1/tools').json()['executable'])
        for mutate in ('missing', 'duplicate', 'self', 'potential', 'core', 'overlap'):
            request = example(); folio = request['folios'][0]
            if mutate == 'missing': folio['wires'][0]['start']['terminal'] = 'absent'
            if mutate == 'duplicate': folio['components'][1]['id'] = 'PT-101'
            if mutate == 'self': folio['wires'][0]['end'] = deepcopy(folio['wires'][0]['start'])
            if mutate == 'potential': folio['components'][1]['terminals'][0]['potential'] = 'CONFLICT'
            if mutate == 'core': folio['wires'][1]['core'] = '1'
            if mutate == 'overlap':
                for c in folio['components']: c.update(x=180, y=180)
            self.assertFalse(bridge.validate(QElectroTechInput.model_validate(request))['passed'], mutate)
        request = example();request['folios'][0]['components'][0]['terminals'].append({'id': '+'})
        self.assertEqual(self.execute(request).status_code, 422)

    def test_readonly_status_checks_and_inspect_no_history(self):
        before = self.client.get(self.base+'/calculations?view=summary').json()['total']
        data = example();data['action'] = 'check'
        self.assertTrue(self.execute(data).json()['result']['validation']['passed'])
        self.assertIsNone(self.execute({'action': 'status'}).json()['calculationId'])
        path = STORE/'read.qet';path.write_bytes(bridge.native_project(QElectroTechInput.model_validate(example())))
        result = self.execute({'action': 'inspect', 'path': str(path)}).json()['result']
        self.assertEqual(result['counts']['wires'], 6)
        self.assertEqual(result['missing_connections'], [])
        self.assertEqual(self.execute({'action': 'inspect', 'path': str(STORE.parent/'forbidden.qet')}).status_code, 422)
        path.write_text('<!DOCTYPE a [<!ENTITY x SYSTEM "file:///secret">]><project>&x;</project>', encoding='utf-8')
        self.assertEqual(self.execute({'action': 'inspect', 'path': str(path)}).status_code, 422)
        self.assertEqual(self.client.get(self.base+'/calculations?view=summary').json()['total'], before)

    def test_native_export_artifacts_delivery_and_sources(self):
        response = self.execute(example())
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json(); self.assertTrue(data['calculationId'])
        result = self.client.get(self.base+'/calculations/'+data['calculationId']).json()['result']
        self.assertTrue(result['validation']['passed'])
        self.assertFalse(result['validation']['native_reload_verified'])
        artifacts = data['artifacts']; kinds = {a['kind'] for a in artifacts}
        self.assertTrue({'qet', 'pdf', 'svg', 'csv', 'json'} <= kinds)
        native = next(a for a in artifacts if a['kind'] == 'qet')
        root = ET.fromstring(self.client.get(native['downloadPath']).content)
        self.assertEqual(len(root.findall('collection/category/element')), 3)
        self.assertEqual(len(root.findall('diagram/conductors/conductor')), 6)
        self.assertEqual(len({p.get('id') for p in root.findall('diagram/elements/element/terminals/terminal')}), 8)
        # Native QET matches instance terminals to the symbol's inward dock,
        # rather than to its conductor-side coordinates (terminalSize=4).
        for component in root.findall('diagram/elements/element'):
            filename = component.get('type').split('/')[-1]
            definition = root.find('collection/category/element[@name="'+filename+'"]/definition')
            for instance, pin in zip(component.findall('terminals/terminal'), definition.findall('description/terminal')):
                dx, dy = {'n': (0, 4), 'e': (-4, 0), 's': (0, -4), 'w': (4, 0)}[pin.get('orientation')]
                self.assertAlmostEqual(float(instance.get('x')), float(pin.get('x'))+dx)
                self.assertAlmostEqual(float(instance.get('y')), float(pin.get('y'))+dy)
        for element in root.findall('diagram/elements/element'):
            filename = element.get('type').split('/')[-1]
            self.assertIsNotNone(root.find('collection/category/element[@name="'+filename+'"]'))
        source = next(a for a in artifacts if a['name'] == 'qet-input.json')
        self.assertEqual(source['delivery_role'], 'internal')
        self.assertEqual(self.client.get(source['downloadPath']).json()['source'], example()['source'])
        csv = next(a for a in artifacts if a['kind'] == 'csv')
        self.assertIn(example()['source'], self.client.get(csv['downloadPath']).content.decode('utf-8-sig'))
        project = k.load_project(self.pid)
        project['plan'] = {'deliverables': []}
        selected = delivery.select_files(project)
        self.assertNotIn('qet', {a['kind'] for a in selected})
        self.assertNotIn('json', {a['kind'] for a in selected})
        self.assertEqual(delivery.select_files(project, [native['artifactId']])[0]['kind'], 'qet')

    def test_missing_software_is_structured_and_readonly_check_still_works(self):
        with patch('kernel.dependencies.qelectrotech_status', return_value={'installed': False, 'ready': False, 'path': ''}):
            response = self.execute(example())
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['detail']['dependency'], 'qelectrotech')
            check = example();check['action'] = 'check'
            self.assertTrue(self.execute(check).json()['result']['validation']['passed'])

    def test_native_parser_bounds_and_legacy_phase(self):
        pid = self.client.post('/v1/projects', json={'name': '旧流程接线', 'workflow': 'legacy'}).json()['project']['projectId']
        response = self.client.post('/v1/projects/'+pid+'/execute', json={'tool': 'qelectrotech', 'args': example()})
        self.assertEqual(response.status_code, 409)
        for content in (b'<project><diagram><elements><element><terminals><terminal id="0"/><terminal id="0"/></terminals></element></elements></diagram></project>', b'<notproject/>'):
            path = STORE/'invalid.qet';path.write_bytes(content)
            self.assertEqual(self.execute({'action': 'inspect', 'path': str(path)}).status_code, 422)
        root = ET.fromstring(bridge.native_project(QElectroTechInput.model_validate(example())))
        terminal = root.find('diagram/elements/element/terminals/terminal')
        terminal.set('x', str(float(terminal.get('x'))+10))
        path = STORE/'broken-binding.qet';path.write_bytes(ET.tostring(root))
        self.assertTrue(self.execute({'action': 'inspect', 'path': str(path)}).json()['result']['missing_connections'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
