"""Optional-install workflow regression; never downloads or installs software."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
temporary = tempfile.TemporaryDirectory(prefix='chemellm-dependency-tests-')
os.environ.update(CHEMELLM_DATA_ROOT=temporary.name,
                  CHEMELLM_KERNEL_DATA=str(Path(temporary.name)/'kernel'),
                  CHEMELLM_DATA_DIR=str(Path(temporary.name)/'kernel'),
                  CHEMELLM_SNAPSHOT_DIR=str(Path(temporary.name)/'.chem'),
                  CHEMELLM_KERNEL_TOKEN='', CHEMELLM_PHASE_GATE='enforce')

from fastapi.testclient import TestClient  # noqa: E402
from kernel import dependencies as deps  # noqa: E402
from kernel import server  # noqa: E402


def state(ready=False, dependency='cantera'):
    return {'dependency': dependency, **deps.CATALOG[dependency], 'installed': ready, 'ready': ready, 'path': 'test-only'}


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=temporary.name)
        self.manager = deps.DependencyManager(Path(self.folder.name), ROOT)
        self.recipe = {'commands': [['mock-installer', '--test-only']], 'method': 'mock', 'target': self.folder.name,
                       'version': 'test', 'package_source': 'https://example.com/test', 'effects': ['仅供测试']}
        self.probe = patch.object(deps, 'probe', return_value=state()).start()
        self.recipe_mock = patch.object(self.manager, '_recipe', return_value=self.recipe).start()
        self.runner = patch.object(deps, '_run', return_value=subprocess.CompletedProcess([], 0)).start()
        self.verify = patch.object(self.manager, '_verify', return_value={'passed': True, 'scope': 'mock interface test'}).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.folder.cleanup)

    def plan(self):
        result = self.manager.prepare('cantera', deps.DependencyPrepare(reason='本任务需要气相反应动力学积分，当前解释器缺少 Cantera'))
        self.assertEqual(result['status'], 'awaiting_consent')
        self.assertIn('是否安装', result['question'])
        return result['plan']

    def wait_job(self, job_id):
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            result = self.manager.install_status(job_id)
            if result['status'] not in {'installing', 'verifying'}:
                return result
            time.sleep(.01)
        self.fail('模拟安装任务未结束')

    def test_check_prepare_no_or_absent_consent_never_install(self):
        self.manager.check('cantera')
        plan = self.plan()
        for request in [deps.DependencyInstall(plan_id=plan['plan_id']),
                        deps.DependencyInstall(plan_id=plan['plan_id'], confirmed=False, user_response='否'),
                        deps.DependencyInstall(plan_id=plan['plan_id'], confirmed=True),
                        deps.DependencyInstall(plan_id=plan['plan_id'], confirmed=True, user_response='No!')]:
            with self.assertRaises(PermissionError): self.manager.install(request)
        self.runner.assert_not_called()
        self.assertFalse((self.manager.root/'jobs').exists())
        self.assertEqual(json.loads((self.manager.root/'plans'/(plan['plan_id']+'.json')).read_text(encoding='utf-8'))['reason'], plan['reason'])

    def test_yes_installs_once_verifies_records_consent_and_resumes(self):
        plan = self.plan()
        self.probe.side_effect = [state(), state(True)]
        request = deps.DependencyInstall(plan_id=plan['plan_id'], confirmed=True, user_response='是，请按上述方案安装 Cantera')
        started = self.manager.install(request)
        completed = self.wait_job(started['job_id'])
        self.assertEqual(completed['status'], 'succeeded', completed)
        self.assertTrue(completed['verification']['passed'])
        self.assertEqual(completed['user_response'], request.user_response)
        self.assertIn('继续原任务', completed['note'])
        self.assertTrue(Path(completed['log_path']).is_file())
        self.manager.install(request)
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(self.runner.call_args.args[0], self.recipe['commands'][0])

    def test_failure_or_timeout_is_not_success_and_same_job_does_not_retry(self):
        for error in [subprocess.TimeoutExpired('mock-installer', 1800), RuntimeError('synthetic denied')]:
            plan = self.plan()
            self.runner.side_effect = error
            request = deps.DependencyInstall(plan_id=plan['plan_id'], confirmed=True, user_response='是')
            job = self.manager.install(request)
            failed = self.wait_job(job['job_id'])
            self.assertEqual(failed['status'], 'needs_attention')
            self.assertIn('error', failed)
            before = self.runner.call_count
            self.assertEqual(self.manager.install(request)['status'], 'needs_attention')
            self.assertEqual(self.runner.call_count, before)

    def test_plan_tampering_and_duplicate_concurrent_installs(self):
        plan = self.plan()
        path = self.manager.root/'plans'/(plan['plan_id']+'.json')
        edited = json.loads(path.read_text(encoding='utf-8'))
        edited['commands'] = [['malicious-script']]
        path.write_text(json.dumps(edited), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.manager.install(deps.DependencyInstall(plan_id=plan['plan_id'], confirmed=True, user_response='是'))
        self.runner.assert_not_called()
        plan = self.plan()
        ready, release = threading.Event(), threading.Event()
        def block(*args, **kwargs):
            ready.set()
            release.wait(5)
            return subprocess.CompletedProcess([], 0)
        self.runner.side_effect = block
        self.probe.return_value = state()
        other = self.plan()
        job = self.manager.install(deps.DependencyInstall(plan_id=plan['plan_id'], confirmed=True, user_response='是'))
        self.assertTrue(ready.wait(2))
        try:
            with self.assertRaisesRegex(ValueError, '已有安装任务'):
                self.manager.install(deps.DependencyInstall(plan_id=other['plan_id'], confirmed=True, user_response='是'))
        finally:
            self.probe.return_value = state(True)
            release.set()
            self.wait_job(job['job_id'])
        self.assertEqual(self.runner.call_count, 1)

    def test_already_installed_or_no_install_method(self):
        self.probe.return_value = state(True)
        self.assertEqual(self.manager.prepare('cantera', deps.DependencyPrepare(reason='task'))['status'], 'already_available')
        self.probe.return_value = state()
        self.recipe_mock.side_effect = ValueError('没有 WinGet')
        self.assertEqual(self.manager.prepare('cantera', deps.DependencyPrepare(reason='task'))['status'], 'manual_required')
        self.runner.assert_not_called()

    def test_http_missing_tool_provides_consent_flow_without_install(self):
        with TestClient(server.app) as client:
            project = client.post('/v1/projects', json={'name': '缺少依赖测试'}).json()['project']['projectId']
            try:
                response = client.post('/v1/projects/'+project+'/execute', json={
                    'tool': 'cantera_equilibrium', 'args': {'temperature_K': 1000}})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['detail']['dependency'], 'cantera')
                response = client.post('/v1/dependency-installs', json={'plan_id': '0'*32})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(client.get('/v1/dependencies?dependency=not-supported').status_code, 422)
            finally:
                client.delete('/v1/projects/'+project)
        self.runner.assert_not_called()


class RecipeTests(unittest.TestCase):
    def test_fixed_recipes_environment_and_scope(self):
        manager = deps.DependencyManager(Path(temporary.name), ROOT)
        with patch.object(deps.shutil, 'which', return_value='C:/WindowsApps/winget.exe'):
            for dependency in ('dwsim', 'freecad', 'libreoffice'):
                recipe = manager._recipe(dependency)
                command = recipe['commands'][0]
                self.assertEqual(command[command.index('--id')+1], deps.CATALOG[dependency]['package_id'])
                self.assertIn('--exact', command)
            with patch.object(sys, 'prefix', str(ROOT/'.venv')), patch.object(deps.importlib.util, 'find_spec', return_value=object()):
                command = manager._recipe('cantera')['commands'][0]
                self.assertEqual(command[0], sys.executable)
                self.assertIn('cantera==3.2.0', command)
                self.assertIn('--only-binary=:all:', command)
        with patch.dict(os.environ, {'PIP_EXTRA_INDEX_URL': 'https://invalid.example', 'PIP_TARGET': 'other', 'PYTHONUSERBASE': 'other'}):
            environment = manager._installer_env()
            self.assertNotIn('PIP_EXTRA_INDEX_URL', environment)
            self.assertNotIn('PIP_TARGET', environment)
            self.assertNotIn('PYTHONUSERBASE', environment)


if __name__ == '__main__':
    try: unittest.main(verbosity=2)
    finally: temporary.cleanup()
