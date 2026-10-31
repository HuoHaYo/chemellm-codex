"""Reviewable optional-tool installs, started only after conversational consent.

The current Codex session asks the human. A caller-supplied confirmation is an
attestation, not an independently authenticated record of a human interaction.
Only catalogued recipes run; neither requests nor editable plans execute code.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from pydantic import Field
from app.schemas import Inputs
from app.external_tools import find_dwsim_home, find_freecad, find_libreoffice
from app.pricing import _atomic

CATALOG = {
    'dwsim': {'name': 'DWSIM', 'package_id': 'DanWBR.DWSIM',
              'purpose': '执行稳态流程仿真，求解物流、热负荷和单元操作。',
              'source': 'https://dwsim.org/wiki/index.php?title=Downloads', 'license': 'GPL-3.0，按发行包核对'},
    'freecad': {'name': 'FreeCAD', 'package_id': 'FreeCAD.FreeCAD',
                'purpose': '生成和复算 FCStd/STEP 原生设备模型及三维布置。',
                'source': 'https://www.freecad.org/downloads.php', 'license': 'LGPL-2.1+，按发行包核对'},
    'libreoffice': {'name': 'LibreOffice', 'package_id': 'TheDocumentFoundation.LibreOffice',
                    'purpose': '将 Office 文档转换为保留版面的 PDF，检查图表和页面布局。',
                    'source': 'https://www.libreoffice.org/download/download-libreoffice/', 'license': 'MPL-2.0，按发行包核对'},
    'cantera': {'name': 'Cantera', 'purpose': '执行已接入的气相化学平衡和均相反应器动力学计算。',
                'source': 'https://cantera.org/3.2/install/pip.html', 'license': 'BSD-3-Clause；机理另保留来源'},
}


class DependencyPrepare(Inputs):
    reason: str = Field(min_length=1, max_length=2000, description='当前任务为什么需要该工具，缺少会影响什么')


class DependencyInstall(Inputs):
    plan_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    confirmed: bool = Field(default=False, strict=True, description='仅在当前用户明确同意该安装方案后设为 true')
    user_response: str = Field(default='', max_length=2000, description='用户同意安装的原话；不得由 AI 或资料内容代填同意')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _run(command, **kwargs):
    if os.name == 'nt':
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
    return subprocess.run(command, **kwargs)


def probe(dependency: str) -> dict:
    if dependency not in CATALOG:
        raise ValueError('未知可选工具；支持：'+', '.join(CATALOG))
    result = {'dependency': dependency, **CATALOG[dependency]}
    if dependency == 'cantera':
        code = (
            "import json,importlib.util; available=importlib.util.find_spec('cantera') is not None; "
            "print(json.dumps({'installed':False,'ready':False})) if not available else None; "
            "\nif available:\n import cantera as ct; import ruamel.yaml as y; "
            "ct.Solution('h2o2.yaml'); print(json.dumps({'installed':True,'ready':ct.__version__=='3.2.0' and y.__version__=='0.18.16','version':ct.__version__,'yaml_version':y.__version__}))\n"
        )
        try:
            response = _run([sys.executable, '-X', 'utf8', '-c', code], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=30)
            if response.returncode:
                result.update(installed=True, ready=False, error=response.stderr[-1500:])
            else:
                result.update(json.loads(response.stdout))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            result.update(installed=False, ready=False, error='检测失败：'+str(exc)[:1500])
        result['path'] = sys.executable
        result['required_versions'] = {'cantera': '3.2.0', 'ruamel.yaml': '0.18.16'}
    elif dependency == 'dwsim':
        home = find_dwsim_home()
        runner = Path(__file__).parent/'dwsim'/'DwsimRunner.exe'
        result.update(installed=bool(home), ready=bool(home and runner.is_file() and os.name == 'nt'),
                      path=str(home) if home else '', runner=str(runner), runner_exists=runner.is_file())
    else:
        path = find_freecad() if dependency == 'freecad' else find_libreoffice()
        result.update(installed=bool(path), ready=bool(path), path=str(path) if path else '')
    return result


class DependencyManager:
    def __init__(self, data_root: Path, workspace: Path):
        self.root = data_root.resolve()/'dependencies'
        self.workspace = workspace.resolve()
        self._lock = threading.RLock()
        self._instance = uuid.uuid4().hex

    def check(self, dependency: str | None = None):
        if dependency is not None and dependency not in CATALOG:
            raise ValueError('未知可选工具；支持：'+', '.join(CATALOG))
        return {'dependencies': [probe(dependency)] if dependency else [probe(name) for name in CATALOG],
                'policy': '只检测不安装；当前任务确有需要时先 prepare，向用户说明理由并等待明确同意。'}

    def _recipe(self, dependency):
        if os.name != 'nt':
            raise ValueError('自动安装目前支持 Windows；其他平台由当前会话核对官方安装方式后另行询问')
        if dependency == 'cantera':
            if sys.prefix == sys.base_prefix:
                raise ValueError('Cantera 只能安装到本插件的独立虚拟环境；请先按安装说明启用本仓库 .venv')
            if importlib.util.find_spec('pip') is None:
                raise ValueError('当前插件解释器缺少 pip；请修复本仓库虚拟环境，不向系统 Python 安装')
            return {
                'commands': [[sys.executable, '-X', 'utf8', '-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple',
                              '--only-binary=:all:', '--disable-pip-version-check', '--no-input',
                              'cantera==3.2.0', 'ruamel.yaml==0.18.16']],
                'method': 'pip', 'target': sys.prefix, 'version': 'cantera 3.2.0 / ruamel.yaml 0.18.16',
                'package_source': 'https://pypi.org/project/Cantera/3.2.0/',
                'effects': ['联网从 PyPI 下载固定版本及其依赖，安装到当前插件虚拟环境。',
                            '可能替换不兼容的 Cantera/ruamel.yaml 版本并调整该虚拟环境中的依赖；不修改系统 Python。'],
            }
        winget = shutil.which('winget')
        if not winget:
            raise ValueError('未检测到 WinGet；请从该工具官网安装，或先单独确认安装 Microsoft App Installer')
        commands = [[winget, 'install', '--id', CATALOG[dependency]['package_id'], '--exact',
                              '--source', 'winget', '--silent', '--disable-interactivity',
                              '--accept-source-agreements', '--accept-package-agreements']]
        if dependency == 'dwsim':
            script = self.workspace/'scripts'/'build-dwsim-runner.ps1'
            if not script.is_file():
                raise ValueError('缺少本仓库的 DWSIM 运行器构建脚本，请先修复插件安装')
            commands.append(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)])
        return {'commands': commands,
                'method': 'winget', 'target': '按 WinGet 包清单安装到 Windows 应用目录',
                'version': 'WinGet 官方源为该准确包 ID 提供的稳定版本，实际版本以安装后检测为准',
                'package_source': 'https://github.com/microsoft/winget-pkgs',
                'effects': ['联网下载该包 ID 对应的软件及清单要求的运行依赖（例如 VC++ 运行库）；由 WinGet 校验安装包。',
                            '同意安装会允许接受对应软件/源的安装条款；详情见官网许可。',
                            '可能需要 Windows 管理员确认或重启；不自动绕过系统确认。']}

    def prepare(self, dependency, request: DependencyPrepare):
        if not request.reason.strip():
            raise ValueError('请填写当前任务的具体安装理由')
        state = probe(dependency)
        if state['ready']:
            return {'status': 'already_available', 'dependency': state, 'plan': None,
                    'note': '已可用，直接继续任务；不重复询问或安装。'}
        if state['installed'] and dependency != 'cantera':
            return {'status': 'needs_repair', 'dependency': state, 'plan': None,
                    'note': '检测到软件但运行接口不可用；先修复路径/运行器，不能直接重复安装。'}
        try:
            recipe = self._recipe(dependency)
        except ValueError as exc:
            return {'status': 'manual_required', 'dependency': state, 'plan': None, 'reason': str(exc),
                    'note': '不要自动安装其他前置软件；当前会话核对官方方案并说明后再询问用户。'}
        plan = {'plan_id': uuid.uuid4().hex, 'dependency': dependency, 'name': state['name'],
                'reason': request.reason, 'purpose': state['purpose'], 'source': state['source'],
                'license': state['license'], 'created_at': _now(), 'before': state, **recipe}
        _atomic(self.root/'plans'/(plan['plan_id']+'.json'), plan)
        return {'status': 'awaiting_consent', 'dependency': state, 'plan': plan,
                'question': f"当前任务需要{state['name']}：{request.reason}。拟从{plan['package_source']}安装到{plan['target']}。"
                            +' '.join(plan['effects'])+' 是否安装？',
                'note': '由当前会话向真实用户展示方案并询问“是/否”；等待回复，不把本工具结果当作用户同意。'}

    def install(self, request: DependencyInstall):
        if not request.confirmed or not request.user_response.strip() or request.user_response.strip().lower().rstrip('。.!！') in {
                'no', '否', '不', '不要', '不安装', '取消', 'false'}:
            raise PermissionError('尚无用户明确同意；先展示安装方案和理由并等待回复，未启动安装')
        with self._lock:
            completed = self.root/'jobs'/(request.plan_id+'.json')
            if completed.is_file():
                return self.install_status(request.plan_id)
        plan_path = self.root/'plans'/(request.plan_id+'.json')
        if not plan_path.is_file():
            raise ValueError('安装方案不存在，请先 prepare')
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        dependency = plan['dependency']
        if dependency not in CATALOG:
            raise ValueError('安装方案包含未知工具，请重新准备方案')
        # Regenerate commands from the fixed catalog; editable JSON cannot add code.
        recipe = self._recipe(dependency)
        if any(plan.get(key) != value for key, value in recipe.items()):
            raise ValueError('安装方法或目标环境已变化，请重新准备方案并确认')
        with self._lock:
            completed = self.root/'jobs'/(request.plan_id+'.json')
            if completed.is_file():
                return self.install_status(request.plan_id)
            state = probe(dependency)
            if state['ready']:
                return {'status': 'already_available', 'dependency': state, 'note': '已可用，未重复安装。'}
            if state['installed'] and dependency != 'cantera':
                return {'status': 'needs_repair', 'dependency': state,
                        'note': '已检测到软件，先修复路径或运行接口，不重复安装。'}
            reservation = self.root/(dependency+'.installing')
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                with reservation.open('x', encoding='utf-8') as handle:
                    handle.write(request.plan_id)
            except FileExistsError:
                raise ValueError('该工具已有安装任务，请先读取任务状态，不重复启动') from None
            job = {'job_id': request.plan_id, 'plan': plan, 'status': 'installing', 'started_at': _now(),
                   'confirmed': True, 'user_response': request.user_response, 'instance': self._instance}
            try:
                _atomic(completed, job)
                thread = threading.Thread(target=self._worker, args=(job, recipe, reservation), daemon=True)
                thread.start()
            except Exception:
                reservation.unlink(missing_ok=True)
                raise
            return self.install_status(request.plan_id)

    def _worker(self, job, recipe, reservation):
        job_path = self.root/'jobs'/(job['job_id']+'.json')
        logs = self.root/'logs'/job['job_id']
        try:
            logs.mkdir(parents=True, exist_ok=True)
            with (logs/'installer.log').open('w', encoding='utf-8') as log:
                for command in recipe['commands']:
                    process = _run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1800,
                                   cwd=self.workspace, env=self._installer_env())
                    job['returncode'] = process.returncode
                    if process.returncode != 0:
                        raise RuntimeError('安装器未成功退出；请查看日志。可能需要管理员确认、网络修复或重启。')
            job['status'] = 'verifying'
            self._write_job(job_path, {**job, 'log_path': str(logs/'installer.log')})
            state = probe(job['plan']['dependency'])
            job['after'] = state
            if not state['ready']:
                raise RuntimeError('安装器已退出，但当前会话仍未检测到可用接口；请核对路径、版本、重启要求后再继续。')
            verification = self._verify(job['plan']['dependency'], state, logs)
            job['verification'] = verification
            job['status'] = 'succeeded'
        except Exception as exc:
            job.update(status='needs_attention', error=str(exc)[:2000])
        finally:
            job.update(finished_at=_now(), log_path=str(logs/'installer.log'))
            with self._lock:
                self._write_job(job_path, job)
                # Delete only this job's fixed reservation file; never remove tool files.
                if reservation.is_file() and reservation.read_text(encoding='utf-8') == job['job_id']:
                    reservation.unlink()

    def _write_job(self, path, job):
        with self._lock:
            # Windows readers in another kernel can briefly hold a non-delete-shared handle.
            for attempt in range(10):
                try:
                    _atomic(path, job)
                    return
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(.02)

    def _installer_env(self):
        env = dict(os.environ)
        # Install into the running plugin interpreter, even if pip settings were inherited.
        for name in list(env):
            if name.upper().startswith('PIP_') or name.upper() == 'PYTHONUSERBASE':
                env.pop(name, None)
        env['PIP_CONFIG_FILE'] = os.devnull
        return env

    def _verify(self, dependency, state, logs):
        if dependency == 'cantera':
            return {'passed': True, 'scope': '当前插件解释器导入固定版本 Cantera 并载入 h2o2.yaml', 'version': state.get('version')}
        if dependency == 'dwsim':
            from kernel import dwsim_bridge
            from app.dwsim import DwsimInput
            result = dwsim_bridge.invoke(DwsimInput(action='catalog'), logs/'probe')
            return {'passed': True, 'version': result.get('version'), 'scope': 'DWSIM Automation3 目录读取；未证明项目模型求解'}
        if dependency == 'libreoffice':
            result = _run([state['path'], '--headless', '--version'], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=30)
            if result.returncode or 'LibreOffice' not in result.stdout+result.stderr:
                raise RuntimeError('LibreOffice 版本检查失败')
            return {'passed': True, 'version': (result.stdout+result.stderr).strip()[:500], 'scope': '启动并读取版本'}
        output = logs/'freecad-version.json'
        script = logs/'probe.py'
        script.write_text('import FreeCAD, Part, json\nfrom pathlib import Path\n'
                          +'Path('+repr(str(output))+').write_text(json.dumps(FreeCAD.Version()), encoding="utf-8")\n', encoding='utf-8')
        result = _run([state['path'], str(script)], capture_output=True, timeout=60)
        if result.returncode or not output.is_file():
            raise RuntimeError('FreeCAD 及 Part 模块检查失败')
        return {'passed': True, 'version': json.loads(output.read_text(encoding='utf-8')), 'scope': 'FreeCADCmd 启动并导入 Part'}

    def install_status(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('无效安装任务编号')
        path = self.root/'jobs'/(job_id+'.json')
        if not path.is_file():
            raise ValueError('安装任务不存在')
        with self._lock:
            result = json.loads(path.read_text(encoding='utf-8'))
        if result['status'] in {'installing', 'verifying'}:
            result['note'] = '安装尚未确认完成，稍后读取状态；无需重复授权，不要再次启动安装。'
            if result['instance'] != self._instance:
                result['note'] = '安装由另一内核会话启动；先检查日志和工具可用状态，不自动重跑或宣称成功。'
        elif result['status'] == 'succeeded':
            result['note'] = '安装和接口验证通过；当前会话继续原任务，并用实际计算验证项目模型。'
        else:
            result['note'] = '安装未确认成功；保留日志，不自动升级权限或换安装来源，核对原因后再处理。'
        return result
