"""Verify delivery ZIP bytes using an optional external or legacy internal manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def newest_bundle():
    bundles = sorted((ROOT / 'data' / 'bundles').glob('*.zip'), key=lambda p: p.stat().st_mtime)
    if not bundles:
        raise ValueError('no bundle under data/bundles')
    return bundles[-1]


def verify(target, manifest_path=None):
    target = Path(target)
    with zipfile.ZipFile(target) as archive:
        if len(set(archive.namelist())) != len(archive.namelist()):
            raise ValueError('duplicate ZIP entry')
        failed = archive.testzip()
        if failed:
            raise ValueError('ZIP integrity failure: '+failed)
        manifest = None
        candidate = Path(manifest_path) if manifest_path else target.with_suffix('.manifest.json')
        if candidate.is_file():
            manifest = json.loads(candidate.read_text(encoding='utf-8'))
        elif manifest_path:
            raise ValueError('manifest not found: '+str(candidate))
        elif 'manifest.json' in archive.namelist():
            manifest = json.loads(archive.read('manifest.json'))
        if manifest is None:
            return {'integrity': True, 'hashesVerified': False, 'note': '仅检查 ZIP 压缩完整性；无清单，未验证文件哈希或成果完整性。'}
        if manifest.get('bundleSha256'):
            digest = hashlib.sha256()
            with target.open('rb') as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(block)
            if digest.hexdigest() != manifest['bundleSha256']:
                raise ValueError('bundle sha256 mismatch')
        names = set(archive.namelist())
        for item in manifest.get('files') or []:
            if item.get('missing') or item.get('skipped'):
                continue  # Old archives may record omissions explicitly.
            name = item.get('archivePath')
            if name not in names:
                raise ValueError('manifest references missing file: '+str(name))
            digest = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(block)
            if digest.hexdigest() != item.get('sha256'):
                raise ValueError('sha256 mismatch: '+name)
        if manifest.get('schemaVersion') == 2 and names != {f['archivePath'] for f in manifest['files']}:
            raise ValueError('manifest does not cover all ZIP entries')
        return {'integrity': True, 'hashesVerified': True, 'files': len(manifest.get('files') or [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path, nargs='?')
    parser.add_argument('--manifest', type=Path, help='External manifest; defaults to adjacent .manifest.json')
    args = parser.parse_args()
    try:
        result = verify(args.path or newest_bundle(), args.manifest)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
