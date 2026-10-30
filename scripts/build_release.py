"""Build an allowlisted, deterministic Windows installer ZIP from Git-tracked files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {"app", "kernel", "chemellm_codex", "codex-plugin", "scripts", "release", ".agents", "docs"}
FILES = {"README.md", "INSTALL.cmd", "CHECK.cmd", "安装使用说明.md", "pyproject.toml", "AGENTS.md"}


def allowed(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    if name == "codex-plugin/.mcp.json" or any(p in {"__pycache__", ".venv", "data", ".chem", "runtime"} for p in path.parts):
        return False
    return name in FILES or path.parts[0] in DIRECTORIES


def build(destination: Path) -> Path:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    plugin = json.loads((ROOT / "codex-plugin/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if plugin["version"] != version:
        raise ValueError("Plugin and Python package versions differ")
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    names = sorted(name for name in files if name and allowed(name))
    for required in ["INSTALL.cmd", "CHECK.cmd", "scripts/install-windows.ps1", "release/requirements-windows-x64.lock", "安装使用说明.md"]:
        if required not in names:
            raise ValueError(f"Required release file is not tracked: {required}; stage source files first")
    folder = f"ChemELLM-Codex-{version}-windows-x64"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (folder + ".zip")
    manifest = {"version": version, "platform": "windows-x64", "files": []}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            content = (ROOT / name).read_bytes()
            # Windows PowerShell 5.1 expects UTF-8 BOM for non-ASCII source.
            if name.endswith(".ps1"):
                content = content.removeprefix(b"\xef\xbb\xbf")
                content = b"\xef\xbb\xbf" + content
            if name.endswith(".cmd"):
                content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            info = zipfile.ZipInfo(folder + "/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
            manifest["files"].append({"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        info = zipfile.ZipInfo(folder + "/release-manifest.json", date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "SHA256SUMS.txt").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    print(f"Built {target} ({target.stat().st_size:,} bytes, {len(names)} files)")
    print(f"SHA256 {digest}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    build(parser.parse_args().output)


if __name__ == "__main__":
    main()
