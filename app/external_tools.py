"""Shared discovery for optional, separately installed engineering tools."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import json
import subprocess
from functools import lru_cache


def _first(candidates):
    return next((Path(path).resolve() for path in candidates if path and Path(path).is_file()), None)


def find_freecad() -> Path | None:
    candidates = [shutil.which('FreeCADCmd'), shutil.which('freecadcmd')]
    if os.environ.get('FREECAD_HOME'):
        home = Path(os.environ['FREECAD_HOME'])
        candidates += [home/'bin'/'FreeCADCmd.exe', home/'FreeCADCmd.exe']
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData'/'Local')))
    candidates += sorted(local.glob('Programs/FreeCAD*/bin/FreeCADCmd.exe'), reverse=True)
    candidates += sorted(Path(os.environ.get('ProgramFiles', r'C:\Program Files')).glob('FreeCAD*/bin/FreeCADCmd.exe'), reverse=True)
    return _first(candidates)


def find_blender() -> Path | None:
    candidates = []
    if os.environ.get('BLENDER_HOME'):
        home = Path(os.environ['BLENDER_HOME'])
        candidates += [home, home/'blender.exe', home/'blender', home/'Contents'/'MacOS'/'Blender']
    candidates += [shutil.which('blender')]
    for root in [Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'Blender Foundation',
                 Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData'/'Local')))/'Programs'/'Blender Foundation']:
        candidates += sorted(root.glob('Blender*/blender.exe'), reverse=True)
    candidates += [Path('/Applications/Blender.app/Contents/MacOS/Blender'),
                   Path('/usr/bin/blender'), Path('/snap/bin/blender')]
    return _first(candidates)


@lru_cache(maxsize=8)
def _blender_capabilities(path: str, modified: int) -> dict:
    code = (
        "import bpy,json; bpy.ops.export_scene.gltf.get_rna_type(); "
        "engines=[]\n"
        "for name in ['BLENDER_EEVEE_NEXT','BLENDER_EEVEE']:\n"
        " try:\n  bpy.context.scene.render.engine=name; engines.append(name)\n"
        " except TypeError: pass\n"
        "print('CHEMELLM_PROBE='+json.dumps(dict(version=bpy.app.version_string,"
        "ready=bool(engines) and bpy.app.version >= (4,1,0),engines=engines)))"
    )
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    result = subprocess.run([path, '--background', '--factory-startup', '--disable-autoexec',
                             '--python-exit-code', '1', '--python-expr', code],
                            capture_output=True, stdin=subprocess.DEVNULL, cwd=Path(path).parent,
                            text=True, encoding='utf-8', errors='replace', timeout=30, **kwargs)
    if result.returncode:
        raise RuntimeError((result.stdout+result.stderr)[-1500:])
    line = next(line for line in result.stdout.splitlines() if line.startswith('CHEMELLM_PROBE='))
    return json.loads(line.split('=', 1)[1])


def blender_status() -> dict:
    path = find_blender()
    result = {'installed': bool(path), 'ready': False, 'path': str(path) if path else ''}
    if path:
        try:
            result.update(_blender_capabilities(str(path), path.stat().st_mtime_ns))
        except (OSError, ValueError, StopIteration, RuntimeError, subprocess.TimeoutExpired) as exc:
            result['error'] = 'Blender 后台/glTF/Eevee 检测失败：'+str(exc)[-1500:]
    return result


def find_libreoffice() -> Path | None:
    candidates = [shutil.which('soffice'), shutil.which('libreoffice')]
    if os.environ.get('LIBREOFFICE_HOME'):
        home = Path(os.environ['LIBREOFFICE_HOME'])
        candidates += [home/'program'/'soffice.exe', home/'soffice.exe']
    for variable, default in [('ProgramFiles', r'C:\Program Files'), ('ProgramFiles(x86)', r'C:\Program Files (x86)')]:
        candidates.append(Path(os.environ.get(variable, default))/'LibreOffice'/'program'/'soffice.exe')
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData'/'Local')))
    candidates += [local/'Programs'/'LibreOffice'/'program'/'soffice.exe', local/'LibreOffice'/'program'/'soffice.exe']
    return _first(candidates)


def find_dwsim_home() -> Path | None:
    candidates = [Path(os.environ['DWSIM_HOME'])] if os.environ.get('DWSIM_HOME') else []
    if os.environ.get('LOCALAPPDATA'):
        candidates.append(Path(os.environ['LOCALAPPDATA'])/'DWSIM')
    candidates += [Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'DWSIM',
                   Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))/'DWSIM']
    return next((home.resolve() for home in candidates if (home/'DWSIM.Automation.dll').is_file()), None)
