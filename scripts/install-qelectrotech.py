"""Install the hash-pinned official QET payload in the current user's Programs.

Uses an existing 7-Zip; does not execute NSIS, elevate, or change associations.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

URL = 'https://download.qelectrotech.org/qet/tags/20260125/Windows/Installer_QElectroTech-0.100.0_x86_64-win64%2Bgit8590-1.exe'
SHA256 = '14a830e391b78fd7d69d793d13f5f4d24696628b7a682077d3f7332fa41aaa80'
SIZE = 24028187


def seven_zip():
    return shutil.which('7z') or next((str(p) for p in [
        Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'7-Zip/7z.exe',
        Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))/'7-Zip/7z.exe'] if p.is_file()), None)


def download(path):
    # Independent bounded ranges avoid the official mirror's per-connection rate.
    chunk = 524288
    def part(index):
        start, end = index*chunk, min(SIZE, (index+1)*chunk)-1
        for attempt in range(3):
            try:
                request = urllib.request.Request(URL, headers={'User-Agent': 'ChemELLM installer',
                    'Range': f'bytes={start}-{end}'})
                with urllib.request.urlopen(request, timeout=45) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{SIZE}':
                        raise ValueError('官方服务器未提供准确的分段范围')
                    data = response.read(end-start+2)
                if len(data) != end-start+1:
                    raise ValueError('下载片段不完整')
                return data
            except (OSError, ValueError):
                if attempt == 2: raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        with path.open('wb') as stream:
            for data in pool.map(part, range((SIZE+chunk-1)//chunk)):
                stream.write(data)


def install(archive=None):
    if os.name != 'nt':
        raise ValueError('本固定安装方案只支持 Windows x64；其他平台使用官方包')
    extractor = seven_zip()
    if not extractor:
        raise ValueError('需要已有 7-Zip 解包；不自动安装其他前置软件')
    base = Path(os.environ['LOCALAPPDATA']).resolve()/'Programs'
    target = base/'QElectroTech'
    if target.exists():
        raise ValueError('目标已存在，请先检测或修复，不覆盖：'+str(target))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='chemellm-qet-download-') as scratch:
        package = Path(archive).resolve() if archive else Path(scratch)/'installer.exe'
        if not archive: download(package)
        if package.stat().st_size != SIZE or hashlib.sha256(package.read_bytes()).hexdigest() != SHA256:
            raise ValueError('安装包大小或 SHA256 与官方发布不符；未安装')
        with tempfile.TemporaryDirectory(prefix='.chemellm-qet-', dir=base) as staging:
            stage = Path(staging)/'payload'
            result = subprocess.run([extractor, 'x', '-y', '-bd', '-bso0', '-bsp0',
                '-xr!$PLUGINSDIR', '-o'+str(stage), str(package)], capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=180)
            if result.returncode or not (stage/'bin/qelectrotech.exe').is_file() or not (stage/'LICENSE').is_file():
                raise RuntimeError('官方程序解包失败：'+result.stderr.decode('utf-8', 'replace')[-800:])
            probe = subprocess.run([str(stage/'bin/qelectrotech.exe'), '--version'],
                capture_output=True, env={**os.environ, 'QT_QPA_PLATFORM': 'windows'}, cwd=stage,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
            version_output = (probe.stdout+probe.stderr).decode('utf-8', 'replace')
            if probe.returncode or '0.100' not in version_output:
                raise RuntimeError('实际程序版本检查失败：'+version_output[-800:])
            # Both paths are fixed children of the same verified user Programs root.
            if target.parent.resolve() != base.resolve():
                raise ValueError('安装目标越界')
            stage.rename(target)
    return {'installed': True, 'path': str(target/'bin/qelectrotech.exe'),
            'version': '0.100', 'installer_sha256': SHA256, 'source': URL,
            'method': 'official payload / user-local', 'file_associations_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Optional previously downloaded official installer; same hash required')
    args = parser.parse_args()
    print(json.dumps(install(args.archive), ensure_ascii=False))
