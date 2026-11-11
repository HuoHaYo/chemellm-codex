"""Run fixed external workers; publish only complete presentation exports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time

from PIL import Image
from app.blender import BlenderInput
from app.external_tools import find_blender, find_freecad


class SceneConflict(ValueError):
    pass


class BlenderFailure(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_sources(request: BlenderInput, project: dict, workspace: Path, data_root: Path, allowed_roots=()):
    sources, references = {}, {}
    for item in request.items:
        if item.reference:
            ref = item.reference
            obj = project.get('objects', {}).get(ref.key)
            if not obj or obj.get('revision') != ref.revision:
                raise SceneConflict('工程对象不存在或版本已变化：'+ref.key)
            references[ref.key] = obj
        if item.kind == 'cad':
            path = Path(item.path).expanduser()
            path = (workspace/path).resolve() if not path.is_absolute() else path.resolve()
            if not any(path.is_relative_to(root.resolve()) for root in (workspace, data_root, *allowed_roots)):
                raise ValueError('CAD 路径必须位于 MCP roots 或 --allowed-root 允许目录内')
            if path.suffix.lower() not in {'.step', '.stp', '.fcstd'} or not path.is_file():
                raise ValueError('CAD 文件必须是存在的 STEP/STP/FCStd 文件：'+str(path))
            sources[item.id] = path
    if request.render.font_path:
        font = Path(request.render.font_path).expanduser().resolve()
        if not font.is_file() or font.suffix.lower() not in {'.ttf', '.ttc', '.otf'}:
            raise ValueError('font_path 必须指向已有字体文件')
    return sources, references


def _run(command, folder, log_name, deadline, env=None):
    if Path(command[0]).stem.lower() == 'freecadcmd':
        # Some Windows FreeCAD builds abort on non-ASCII script/config CLI paths.
        # Feed an ASCII bootstrap through its Python console; Python then opens
        # the real Unicode path. Keep native startup/profile paths independent
        # of the project, including when its data root contains Chinese text.
        with tempfile.TemporaryDirectory(prefix='chemellm-freecad-') as profile:
            script = str(Path(command[-1]).resolve())
            code = ("import os,sys,traceback\ntry:\n p="+ascii(script)+
                    "\n exec(compile(open(p,encoding='utf-8-sig').read(),p,'exec'),"
                    "{'__name__':'__main__','__file__':p})\n"
                    "except BaseException:\n traceback.print_exc(); sys.stdout.flush(); sys.stderr.flush(); os._exit(1)\n"
                    "else:\n sys.stdout.flush(); sys.stderr.flush(); os._exit(0)\n")
            bootstrap = 'exec('+ascii(code)+')\n'
            launch = [command[0], '--console', '--user-cfg', str(Path(profile)/'user.cfg'),
                      '--system-cfg', str(Path(profile)/'system.cfg')]
            return _run_process(launch, folder, log_name, deadline, env,
                                {'input': bootstrap.encode('ascii'), 'cwd': str(Path(command[0]).parent)})
    return _run_process(command, folder, log_name, deadline, env)


def _run_process(command, folder, log_name, deadline, env=None, extra=None):
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise BlenderFailure('三维导出超过整次 600 秒时限')
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    with (folder/log_name).open('wb') as log:
        try:
            options = {'cwd': folder, 'env': env, 'stdout': log, 'stderr': subprocess.STDOUT,
                       'timeout': remaining, **kwargs, **(extra or {})}
            if 'input' not in options:
                options['stdin'] = subprocess.DEVNULL
            result = subprocess.run(command, **options)
        except subprocess.TimeoutExpired as exc:
            raise BlenderFailure('三维导出超过整次 600 秒时限') from exc
        except OSError as exc:
            raise BlenderFailure('外部程序启动失败：'+str(exc)) from exc
    if result.returncode:
        tail = (folder/log_name).read_text(encoding='utf-8', errors='replace')[-1800:]
        raise BlenderFailure(f'外部程序返回 {result.returncode}：{tail}')


def check_outputs(folder, request):
    blend, glb, png = [folder/name for name in ('scene.blend', 'scene.glb', 'preview.png')]
    # The worker explicitly saves uncompressed native files.
    if not blend.is_file() or blend.stat().st_size < 12:
        raise BlenderFailure('缺少有效 .blend 文件')
    with blend.open('rb') as stream:
        if not stream.read(12).startswith(b'BLENDER'):
            raise BlenderFailure('.blend 文件头无效')
    if not glb.is_file():
        raise BlenderFailure('缺少 GLB 文件')
    with glb.open('rb') as stream:
        header = stream.read(12)
    if len(header) != 12 or struct.unpack('<4sII', header) != (b'glTF', 2, glb.stat().st_size):
        raise BlenderFailure('GLB 文件头或长度无效')
    try:
        with Image.open(png) as im:
            if im.format != 'PNG' or im.size != (request.render.width, request.render.height):
                raise ValueError('PNG 尺寸不匹配')
            im.verify()
    except (OSError, ValueError) as exc:
        raise BlenderFailure('预览 PNG 缺失或损坏') from exc
    report = json.loads((folder/'report.json').read_text(encoding='utf-8'))
    if report.get('item_ids') != [i.id for i in request.items]:
        raise BlenderFailure('生成报告中的对象清单不匹配')
    return report


def build_scene(request, project, workspace, data_root, folder, allowed_roots=()):
    """All paths are local; failed runs keep diagnostics but register no artifacts."""
    sources, references = validate_sources(request, project, workspace, data_root, allowed_roots)
    blender = find_blender()
    freecad = find_freecad() if sources else None
    if not blender or (sources and not freecad):
        raise BlenderFailure('所需 Blender/FreeCAD 不可用')
    deadline = time.monotonic()+600
    folder.mkdir(parents=True, exist_ok=False)
    try:
        original = request.model_dump(mode='json')
        (folder/'request.json').write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding='utf-8')
        reference_snapshot = {'project_id': project['id'], 'objects': references}
        (folder/'references.json').write_text(json.dumps(reference_snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        cad_jobs, source_manifest = [], []
        for index, item in enumerate(request.items):
            if item.id not in sources:
                continue
            path = sources[item.id]
            copied = folder/'sources'/f'{index:04d}'/path.name
            copied.parent.mkdir(parents=True)
            shutil.copyfile(path, copied)
            sha = digest(copied)
            if sha != digest(path):
                raise BlenderFailure('CAD 原件在复制期间变化，请重试')
            original['items'][index]['path'] = copied.relative_to(folder).as_posix()
            cad_jobs.append({'id': item.id, 'path': str(copied), 'object_names': item.object_names})
            source_manifest.append({'id': item.id, 'original_path': str(path),
                                    'snapshot': copied.relative_to(folder).as_posix(), 'sha256': sha})
        original['cad_sources'] = source_manifest
        original['project_id'] = project['id']
        (folder/'scene-input.json').write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding='utf-8')
        # Keep the exact worker sources used by this export for later reproduction.
        shutil.copyfile(Path(__file__).with_name('blender_worker.py'), folder/'blender_worker.py')
        if cad_jobs:
            shutil.copyfile(Path(__file__).with_name('freecad_mesh_worker.py'), folder/'freecad_mesh_worker.py')
            config = {'jobs': cad_jobs, 'deflection_mm': request.tessellation_mm, 'output': str(folder/'cad-mesh.json')}
            config_path = folder/'cad-request.json'
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            env = {**os.environ, 'CHEMELLM_CAD_REQUEST': str(config_path)}
            _run([str(freecad), str(folder/'freecad_mesh_worker.py')], folder, 'freecad.log', deadline, env)
            if not (folder/'cad-mesh.json').is_file():
                raise BlenderFailure('FreeCAD 未生成 CAD 网格')
        _run([str(blender), '--background', '--factory-startup', '--disable-autoexec',
              '--python-exit-code', '1', '--python', str(folder/'blender_worker.py'),
              '--', str(folder/'scene-input.json')], folder, 'blender.log', deadline)
        report = check_outputs(folder, request)
        report['cad_sources'] = source_manifest
        report['references'] = reference_snapshot
        report['source_script_sha256'] = {p.name: digest(p) for p in folder.glob('*worker.py')}
        report['engineering_review'] = 'not_performed'
        report['warnings'] = ['三维成果用于演示；未执行碰撞、检修空间、安全间距或施工配管审核。']
        (folder/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (folder/'reproduce.md').write_text(
            '# 三维演示复现\n\n原始请求见 request.json；可移植场景见 scene-input.json。\n'
            '在本目录运行 Blender：\n\n'
            '    blender --background --factory-startup --disable-autoexec --python-exit-code 1 '
            '--python blender_worker.py -- scene-input.json\n\n'
            'cad-mesh.json 是源 CAD 的固定外观网格，单位 mm；原件副本及哈希见 report.json。\n'
            'references.json 固定工程对象版本。模型及图片仅为演示，不是工程审核结论。\n', encoding='utf-8')
        return report
    except Exception as exc:
        (folder/'failure.json').write_text(json.dumps({'error': str(exc), 'status': 'failed'}, ensure_ascii=False), encoding='utf-8')
        raise BlenderFailure(f'{exc}；诊断目录：{folder}') from exc
