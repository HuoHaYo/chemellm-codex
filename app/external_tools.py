"""Shared discovery for optional, separately installed engineering tools."""
from __future__ import annotations

import os
from pathlib import Path
import shutil


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
