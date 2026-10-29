"""Verify a ChemELLM local bundle: manifest hashes and archive contents.

Usage: python scripts/verify_bundle.py [path-to-zip]
Without a path, verifies the newest zip under data/bundles/.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def newest_bundle() -> Path:
    bundles = sorted((ROOT / "data" / "bundles").glob("*.zip"), key=lambda item: item.stat().st_mtime)
    if not bundles:
        raise SystemExit("no bundle under data/bundles")
    return bundles[-1]


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_bundle()
    if not target.is_file():
        raise SystemExit(f"bundle not found: {target}")
    print("bundle:", target.name, target.stat().st_size, "bytes")
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        print("entries:", len(names))
        for required in ("project.json", "requirements.json", "plan.json", "calculations.json", "manifest.json"):
            if required not in names:
                raise SystemExit(f"missing entry: {required}")
        manifest = json.loads(archive.read("manifest.json"))
        files = manifest.get("files") or []
        missing = [item for item in files if item.get("archivePath") not in names]
        if missing:
            raise SystemExit(f"manifest references missing files: {missing[:3]}")
        mismatched = []
        for item in files:
            archive_path = item.get("archivePath")
            if not archive_path:
                continue
            digest = hashlib.sha256(archive.read(archive_path)).hexdigest()
            if digest != item.get("sha256"):
                mismatched.append(archive_path)
        if mismatched:
            raise SystemExit(f"sha256 mismatch: {mismatched[:3]}")
        print("manifest files:", len(files), "all hashes match")
        print("project phase:", (manifest.get("project") or {}).get("phase"))
        print("VERIFY OK")


if __name__ == "__main__":
    main()
