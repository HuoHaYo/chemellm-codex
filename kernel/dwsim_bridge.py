"""Isolated DWSIM Automation3 process; DWSIM stays an external installation."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import uuid
import zipfile

from app.dwsim import DwsimInput
from app.external_tools import find_dwsim_home

HERE = Path(__file__).resolve().parent
RUNNER = HERE / 'dwsim' / 'DwsimRunner.exe'
MAX_FILE = 25 * 1024 * 1024


def find_home() -> Path | None:
    return find_dwsim_home()


def status() -> dict:
    home = find_home()
    runner = RUNNER.is_file()
    source = RUNNER.with_suffix('.cs')
    needs_build = not runner or (source.is_file() and source.stat().st_mtime > RUNNER.stat().st_mtime)
    samples = sorted([*home.glob('samples/*.dwxmz'), *home.glob('samples/*.dwxml')]) if home else []
    return {'installed': bool(home), 'home': str(home) if home else '',
            'automation_dll': bool(home), 'runner': runner, 'needs_build': needs_build,
            'ready': bool(home and runner and os.name == 'nt'),
            'actions': ['status', 'catalog', 'inspect', 'create', 'run'],
            'samples': [{'name': path.name, 'path': str(path)} for path in samples],
            'scope': '稳态流程；动态、反应集和自定义单元须在原生模型中配置并单独验证',
            'note': '调用本机 DWSIM Automation3，不随插件分发 DWSIM；未运行时不宣称模型已求解'}


def validate_flowsheet(path: Path) -> None:
    if not path.is_file() or path.suffix.lower() not in {'.dwxmz', '.dwxml'}:
        raise ValueError('flowsheet_path 必须是存在的 .dwxmz 或 .dwxml 文件')
    if path.stat().st_size > MAX_FILE:
        raise ValueError('DWSIM 文件不得超过25 MiB')
    if path.suffix.lower() == '.dwxmz':
        try:
            with zipfile.ZipFile(path) as archive:
                files = archive.infolist()
                if len(files) > 2000 or sum(item.file_size for item in files) > 100 * 1024 * 1024:
                    raise ValueError('DWSIM 解压尺寸超限')
                for item in files:
                    name = item.filename.replace('\\', '/')
                    parts = PurePosixPath(name)
                    if parts.is_absolute() or '..' in parts.parts or ':' in name:
                        raise ValueError('DWSIM 压缩包包含越界路径')
        except zipfile.BadZipFile as exc:
            raise ValueError('DWSIM 压缩包格式无效') from exc


def invoke(request: DwsimInput, work_dir: Path, *, save_path: Path | None = None) -> dict:
    home = find_home()
    if home is None:
        raise ValueError('未找到 DWSIM.Automation.dll；请安装 DWSIM 或设置 DWSIM_HOME')
    if not RUNNER.is_file():
        raise ValueError('DWSIM 运行器缺失；运行 scripts/build-dwsim-runner.ps1')
    if os.name != 'nt':
        raise ValueError('当前 DWSIM Automation3 运行器需要 Windows .NET Framework')
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = request.model_dump(mode='json')
    if request.flowsheet_path:
        source = Path(request.flowsheet_path).resolve()
        validate_flowsheet(source)
        payload['flowsheet_path'] = str(source)
    if save_path:
        payload['save_path'] = str(save_path.resolve())
    request_path, output_path = work_dir/'runner-request.json', work_dir/'runner-result.json'
    request_path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    env = {**os.environ, 'TEMP': str(work_dir), 'TMP': str(work_dir)}
    command = [str(RUNNER), str(request_path), str(output_path), str(home)]
    try:
        with (work_dir/'runner.stdout.log').open('w', encoding='utf-8') as stdout, (work_dir/'runner.stderr.log').open('w', encoding='utf-8') as stderr:
            process = subprocess.run(command, stdout=stdout, stderr=stderr, env=env,
                                     timeout=request.timeout_seconds, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'DWSIM 超过 {request.timeout_seconds} 秒，已终止进程；输入和日志保存在 {work_dir}') from exc
    except OSError as exc:
        raise ValueError('DWSIM 运行器启动失败：'+str(exc)) from exc
    if not output_path.is_file():
        raise ValueError(f'DWSIM 未返回结果（退出码 {process.returncode}）；日志：{work_dir}')
    try:
        result = json.loads(output_path.read_text(encoding='utf-8-sig'),
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限数值：'+value)))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('DWSIM 返回无效 JSON；原始结果及日志：'+str(work_dir)) from exc
    if process.returncode != 0:
        raise ValueError('DWSIM 执行失败：'+str(result.get('error') or process.returncode)[:1800]+'；日志：'+str(work_dir))
    if result.get('engine') != 'DWSIM' or result.get('action') != request.action:
        raise ValueError('DWSIM 运行器协议不匹配；请运行 scripts/build-dwsim-runner.ps1')
    result['runner_sha256'] = hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    return result


def catalog(work_dir: Path, *, query='', offset=0, limit=30, timeout=120) -> dict:
    result = invoke(DwsimInput(action='catalog', timeout_seconds=timeout), work_dir/uuid.uuid4().hex)
    compounds = [item for item in result['compounds'] if query.casefold() in
                 ' '.join(str(v) for v in item.values()).casefold()]
    compounds.sort(key=lambda item: item['name'])
    result.update(compounds=compounds[offset:offset+limit], total_compounds=len(compounds), offset=offset,
                  next_offset=offset+limit if offset+limit < len(compounds) else None)
    return result


def run_flowsheet(flowsheet: str | Path, temp_dir: str | Path, timeout: int = 900) -> dict:
    """Compatibility helper used by smoke checks; always save to a new model."""
    work = Path(temp_dir).resolve()/uuid.uuid4().hex
    return invoke(DwsimInput(action='run', flowsheet_path=str(Path(flowsheet).resolve()), timeout_seconds=timeout),
                  work, save_path=work/'solved.dwxmz')


def streams_csv(result: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(['tag', 'name', 'type', 'temperature_K', 'pressure_Pa', 'mass_flow_kg_s', 'compounds', 'mole_fractions'])
    for stream in result.get('streams') or []:
        writer.writerow([*(stream.get(key, '') for key in ('tag', 'name', 'type', 'temperature_k', 'pressure_pa', 'mass_flow_kg_s')),
                         '；'.join(stream.get('compounds') or []),
                         '；'.join(str(value) for value in stream.get('mole_fractions') or [])])
    return buffer.getvalue()
