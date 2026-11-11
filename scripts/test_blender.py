"""Blender unit/API checks and real native/GLB/CAD roundtrips in isolated storage."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = tempfile.TemporaryDirectory(prefix='chemellm-blender-tests-')
STORE = Path(temporary.name)
os.environ.update(CHEMELLM_DATA_ROOT=str(STORE), CHEMELLM_KERNEL_DATA=str(STORE/'kernel'),
                  CHEMELLM_DATA_DIR=str(STORE/'kernel'), CHEMELLM_SNAPSHOT_DIR=str(STORE/'.chem'),
                  CHEMELLM_KERNEL_TOKEN='', CHEMELLM_PHASE_GATE='enforce')
from fastapi.testclient import TestClient
from app.blender import BlenderInput
from app.external_tools import find_blender, find_freecad, blender_status
from kernel import server, blender_bridge as bridge


def request():
    return {'items': [{'id': 'B-1', 'kind': 'box', 'length': 2000, 'width': 1000,
                       'height': 3000, 'position': [4000, 5000, 0], 'source': '测试假设'}],
            'render': {'width': 320, 'height': 180}}


class BlenderTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.pid = self.client.post('/v1/projects', json={'name': 'Blender 隔离测试', 'workflow':'legacy'}).json()['project']['projectId']
        self.base = '/v1/projects/'+self.pid

    def tearDown(self):
        self.client.delete(self.base)
        self.client.close()

    def ready(self):
        self.client.post(self.base+'/requirements', json={'items': [{'code': '3D', 'title': '演示'}]})
        self.client.post(self.base+'/plan', json={'deliverables': [
            {'key': 'scene', 'title': '场景', 'requirementCodes': ['3D'], 'formats': ['blend', 'glb', 'png']} ]})
        self.client.post(self.base+'/objects', json={'kind': 'basis', 'key': 'demo', 'quantities': [
            {'key': 'length', 'value': 2, 'unit': 'm', 'origin': 'assumption',
             'conditions': '虚构测试', 'rationale': '演示假设'}]})

    def execute(self, args):
        return self.client.post(self.base+'/execute', json={'tool': 'export_blender', 'args': args})

    def test_schema_validation_and_phase(self):
        catalog = self.client.get('/v1/tools').json()
        self.assertIn('export_blender', catalog['executable'])
        self.assertIn('items', catalog['schemas']['export_blender']['required'])
        self.assertEqual(self.execute(request()).status_code, 409)
        self.ready()
        invalid = [{}, {'items': []}]
        for mutate in ('duplicate', 'nan', 'negative', 'path'):
            item = request()
            if mutate == 'duplicate': item['items'] *= 2
            if mutate == 'nan': item['items'][0]['height'] = float('inf')
            if mutate == 'negative': item['items'][0]['height'] = -1
            if mutate == 'path': item['items'][0]['points'] = [[0, 0, 0], [1, 0, 0]]
            invalid.append(item)
        for args in invalid:
            with self.assertRaises(ValueError): BlenderInput.model_validate(args)
        self.assertEqual(self.execute({}).status_code, 422)
        self.assertEqual(server.load_project(self.pid)['files'], [])

    def test_references_and_path_boundary(self):
        self.ready()
        args = request()
        args['items'][0]['reference'] = {'key': 'basis/demo', 'revision': 9}
        self.assertEqual(self.execute(args).status_code, 409)
        args['items'][0]['reference']['revision'] = 1
        _, refs = bridge.validate_sources(BlenderInput.model_validate(args), server.load_project(self.pid), ROOT, STORE)
        self.assertEqual(refs['basis/demo']['revision'], 1)
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside)/'outside.step'
            path.write_text('test')
            args = {'items': [{'id': 'CAD', 'kind': 'cad', 'path': str(path), 'source': 'test'}]}
            self.assertEqual(self.execute(args).status_code, 422)

    def test_missing_dependencies_and_failure_do_not_publish(self):
        self.ready()
        with patch('kernel.dependencies.probe', return_value={'ready': False}):
            self.assertEqual(self.execute(request()).json()['detail']['dependency'], 'blender')
        asset = STORE/'dependency.step'
        asset.write_text('test fixture; never opened')
        with patch('kernel.dependencies.probe', side_effect=[{'ready': True}, {'ready': False}]):
            response = self.execute({'items': [{'id': 'CAD', 'kind': 'cad', 'path': str(asset), 'source': 'test'}]})
            self.assertEqual(response.json()['detail']['dependency'], 'freecad')
        with patch('kernel.dependencies.probe', return_value={'ready': True}), \
             patch.object(bridge, 'build_scene', side_effect=bridge.BlenderFailure('test error')):
            self.assertEqual(self.execute(request()).status_code, 500)
        self.assertEqual(server.load_project(self.pid)['files'], [])
        folder = STORE/'timeout'
        folder.mkdir(exist_ok=True)
        with patch.object(bridge.subprocess, 'run', side_effect=subprocess.TimeoutExpired('blender', 1)):
            with self.assertRaises(bridge.BlenderFailure):
                bridge._run(['blender'], folder, 'timeout.log', bridge.time.monotonic()+10)
        self.assertTrue((folder/'timeout.log').is_file())
        with patch.object(bridge.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaises(bridge.BlenderFailure):
                bridge._run(['blender'], folder, 'bad.log', bridge.time.monotonic()+10)
        with patch.object(bridge, 'find_blender', return_value=Path('blender')), \
             patch.object(bridge, '_run', side_effect=bridge.BlenderFailure('test timeout')):
            failed = STORE/'failed-run'
            with self.assertRaises(bridge.BlenderFailure):
                bridge.build_scene(BlenderInput.model_validate(request()), server.load_project(self.pid), ROOT, STORE, failed)
        self.assertEqual(json.loads((failed/'failure.json').read_text(encoding='utf-8'))['status'], 'failed')

    def test_large_generated_bundle(self):
        self.ready()
        project = server.load_project(self.pid)
        server.ensure_legacy_project(project)
        folder = server.KERNEL_DATA/'artifacts'/'large-blender-test'
        folder.mkdir()
        path = folder/'large.blend'
        with path.open('wb') as stream:
            stream.write(b'BLENDER-v401')
            stream.truncate(26*1024*1024)
        record = server.artifact_record(self.pid, path, metadata={'source': 'kernel export_blender',
                                        'scene_group': folder.name, 'scene_path': 'large.blend'})
        project['files'] = [{**server._normalize_artifacts(record, [])[0], 'origin': 'generated'}]
        server.save_project(project)
        bundle = self.client.post(self.base+'/bundle').json()['bundle']
        with zipfile.ZipFile(server.BUNDLES_DIR/bundle['name']) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            entry = manifest['files'][0]
            self.assertNotIn('skipped', entry)
            self.assertEqual(hashlib.sha256(archive.read(entry['archivePath'])).hexdigest(), entry['sha256'])
        # Independently exercise the 256 MiB boundary without creating a huge fixture.
        with patch.object(server, 'MAX_BUNDLE_ARTIFACT', 20*1024*1024):
            skipped = self.client.post(self.base+'/bundle').json()['bundle']
        with zipfile.ZipFile(server.BUNDLES_DIR/skipped['name']) as archive:
            entry = json.loads(archive.read('manifest.json'))['files'][0]
            self.assertEqual(entry['skipped'], 'too_large')
            self.assertEqual(entry['limitBytes'], 20*1024*1024)

    def test_missing_or_corrupt_outputs_rejected(self):
        folder = STORE/'corrupt-output'
        folder.mkdir(exist_ok=True)
        req = BlenderInput.model_validate(request())
        with self.assertRaises(bridge.BlenderFailure): bridge.check_outputs(folder, req)
        (folder/'scene.blend').write_bytes(b'BLENDER-v401')
        (folder/'scene.glb').write_bytes(b'not a GLB')
        with self.assertRaisesRegex(bridge.BlenderFailure, 'GLB'): bridge.check_outputs(folder, req)
        (folder/'scene.glb').write_bytes(struct.pack('<4sII', b'glTF', 2, 12))
        with self.assertRaisesRegex(bridge.BlenderFailure, 'PNG'): bridge.check_outputs(folder, req)

    @unittest.skipUnless(find_blender(), 'Blender not installed')
    def test_real_parametric_units_native_and_glb(self):
        self.ready()
        self.assertTrue(blender_status()['ready'])
        args = request()
        args['items'][0]['reference'] = {'key': 'basis/demo', 'revision': 1}
        with patch.object(bridge, 'find_freecad', side_effect=AssertionError('not needed')):
            response = self.execute(args)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        report = result['result']['report']
        self.assertEqual(report['items'][0]['bounds_m'], [[3.0, 4.5, 0.0], [5.0, 5.5, 3.0]])
        self.assertTrue(report['camera_fits_geometry'])
        folder = server._artifact_path(next(a['artifactId'] for a in result['artifacts'] if a['kind'] == 'blend')).parent
        self.roundtrip(folder, 'B-1')
        # Equivalent metre input must produce the same world-space envelope.
        meter = request()
        meter['units'] = 'm'
        for key in ('length', 'width', 'height'):
            meter['items'][0][key] /= 1000
        meter['items'][0]['position'] = [4, 5, 0]
        meter['render']['camera'] = {'position': [12, -14, 10], 'target': [4, 5, 1.5]}
        old_bundle = self.client.post(self.base+'/bundle').json()['bundle']
        self.assertTrue(old_bundle)
        second = self.execute(meter)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()['result']['report']['items'][0]['bounds_m'], report['items'][0]['bounds_m'])
        self.assertIsNone(server.load_project(self.pid)['bundle'])
        self.assertFalse(server.load_project(self.pid)['review'])

    def roundtrip(self, folder, expected):
        script = folder/'check-roundtrip.py'
        script.write_text(
            'import bpy,json\nfrom pathlib import Path\n'
            f'folder=Path({str(folder)!r})\n'
            'bpy.ops.wm.open_mainfile(filepath=str(folder/"scene.blend"))\n'
            f'assert any(o.get("chemellm_id")=={expected!r} for o in bpy.data.objects)\n'
            'assert bpy.context.scene.unit_settings.scale_length==1\n'
            'from mathutils import Vector\n'
            f'key={expected!r}\n'
            'def bounds():\n'
            ' bpy.context.view_layer.update()\n'
            ' points=[o.matrix_world@Vector(p) for o in bpy.data.objects if o.type=="MESH" and '
            'o.get("chemellm_id")==key and not o.get("is_label") for p in o.bound_box]\n'
            ' return [min(p[i] for p in points) for i in range(3)]+[max(p[i] for p in points) for i in range(3)]\n'
            'native=bounds()\n'
            'bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete(use_global=False)\n'
            'bpy.ops.import_scene.gltf(filepath=str(folder/"scene.glb"))\n'
            f'assert any(o.get("chemellm_id")=={expected!r} for o in bpy.data.objects)\n'
            'assert any(o.get("source") for o in bpy.data.objects)\n'
            'if key.startswith("CAD-"): assert any(o.get("source_sha256") for o in bpy.data.objects)\n'
            'if key=="B-1": assert any(json.loads(o.get("reference","null") or "null")== '
            '{"key":"basis/demo","revision":1} for o in bpy.data.objects)\n'
            'assert all(abs(a-b)<1e-4 for a,b in zip(native,bounds())), (native,bounds())\n', encoding='utf-8')
        bridge._run([str(find_blender()), '--background', '--factory-startup', '--disable-autoexec',
                     '--python-exit-code', '1', '--python', str(script)], folder, 'roundtrip.log', bridge.time.monotonic()+120)

    @unittest.skipUnless(find_blender() and find_freecad(), 'Blender and FreeCAD required')
    def test_real_full_plant_mixed_cad(self):
        self.ready()
        fixture = STORE/'CAD 中文 with spaces'
        fixture.mkdir(exist_ok=True)
        source = fixture/'fixture.py'
        source.write_text(
            'import FreeCAD as A,Part\n'
            'd=A.newDocument("fixture")\n'
            'p=d.addObject("App::Part","Assembly"); p.Placement.Base=A.Vector(1000,0,0)\n'
            'o=d.addObject("Part::Feature","Device"); p.addObject(o)\n'
            'o.Shape=Part.makeBox(2000,1000,3000); o.Placement.Base=A.Vector(500,0,0)\n'
            'd.recompute()\n'
            f'd.saveAs({str(fixture/"设备.FCStd")!r})\n'
            f'Part.export([o],{str(fixture/"设备.step")!r})\n', encoding='utf-8')
        bridge._run([str(find_freecad()), str(source)], fixture, 'fixture.log', bridge.time.monotonic()+120)
        args = json.loads((ROOT/'docs/examples/blender-plant.json').read_text(encoding='utf-8'))
        for index, filename in enumerate(('设备.FCStd', '设备.step')):
            args['items'].append({'id': f'CAD-{index}', 'kind': 'cad', 'path': str(fixture/filename),
                                  'position': [-26000+index*6000, -14000, 0], 'rotation_deg': [0, 0, 90],
                                  'source': '虚构测试 CAD，非真实设备'})
        before = {p.name: bridge.digest(p) for p in fixture.glob('设备.*')}
        response = self.execute(args)
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()['result']['report']
        self.assertTrue(report['camera_fits_geometry'])
        fcstd = next(i for i in report['items'] if i['id'] == 'CAD-0')
        for actual, expected in zip(fcstd['bounds_m'], [[-27, -12.5, 0], [-26, -10.5, 3]]):
            for a, e in zip(actual, expected): self.assertAlmostEqual(a, e, places=4)
        self.assertEqual(before, {p.name: bridge.digest(p) for p in fixture.glob('设备.*')})
        folder = server._artifact_path(next(a['artifactId'] for a in response.json()['artifacts'] if a['kind']=='blend')).parent
        self.roundtrip(folder, 'CAD-0')
        archive_path = self.client.post(self.base+'/bundle').json()['bundle']['name']
        with zipfile.ZipFile(server.BUNDLES_DIR/archive_path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            for entry in manifest['files']:
                self.assertEqual(bridge.hashlib.sha256(archive.read(entry['archivePath'])).hexdigest(), entry['sha256'])
            self.assertTrue(any('/sources/' in n for n in archive.namelist()))
            relocated = STORE/'解包复现 relocated'
            archive.extractall(relocated)
        portable = next((relocated/'scenes').iterdir())
        bridge._run([str(find_blender()), '--background', '--factory-startup', '--disable-autoexec',
                     '--python-exit-code', '1', '--python', str(portable/'blender_worker.py'),
                     '--', str(portable/'scene-input.json')], portable, 'replay.log', bridge.time.monotonic()+120)
        replay = bridge.check_outputs(portable, BlenderInput.model_validate(args))
        self.assertEqual(replay['items'], report['items'])
        # Preserve this explicitly fictional QA scene outside temporary project data for visual review.
        out = ROOT/'output/blender-qa'
        out.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copyfile(folder/'preview.png', out/'preview.png')
        shutil.copyfile(folder/'report.json', out/'report.json')
        for name in ('scene.blend', 'scene.glb'):
            shutil.copyfile(folder/name, out/name)
        shutil.copyfile(server.BUNDLES_DIR/archive_path, out/'demo.zip')


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        temporary.cleanup()
