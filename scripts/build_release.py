"""Build from tracked files and an explicit reviewed source allowlist."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tomllib
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chemellm_codex import skill_fingerprint, skill_inventory
DIRECTORIES = {"app", "kernel", "chemellm_codex", "codex-plugin", "scripts", "release", ".agents", "docs"}
FILES = {"README.md", "INSTALL.cmd", "CHECK.cmd", "EXTERNAL-MCP.cmd", "安装使用说明.md", "pyproject.toml", "AGENTS.md"}
# Explicit source additions make review builds complete before staging, without
# sweeping arbitrary untracked documents or user material into a release.
SOURCE_ADDITIONS = {
    'app/pipe_geometry.py', 'kernel/drawing_style.py', 'scripts/test_visual_standards.py',
    'docs/visual-standardization.md', 'docs/visual-sources.json', 'docs/plugin-backlog.md',
    'codex-plugin/skills/chemellm-engineering/references/visual-standards.md',
    'app/qelectrotech.py', 'kernel/qelectrotech_bridge.py', 'scripts/install-qelectrotech.py',
    'scripts/test_qelectrotech.py', 'docs/qelectrotech.md', 'docs/examples/qelectrotech-loop.json',
    'codex-plugin/skills/chemellm-engineering/references/automation.md',
    'app/basis_selection.py', 'app/blender.py', 'app/efficiency.py', 'app/project_store.py', 'app/delivery.py',
    'kernel/efficient.py', 'kernel/blender_bridge.py', 'kernel/blender_worker.py', 'kernel/freecad_mesh_worker.py',
    'scripts/test_efficiency.py', 'scripts/test_basis_selection.py', 'scripts/test_blender.py', 'scripts/test_delivery.py',
    'scripts/fixtures/stdio_concurrency.py',
    'app/tool_discovery.py', 'scripts/test_skill_efficiency.py', 'scripts/benchmark_skill_efficiency.py',
    'codex-plugin/skills/chemellm-engineering/references/blender.md',
    'codex-plugin/skills/chemellm-engineering/references/numerical-selection.md',
    'codex-plugin/skills/chemellm-engineering/references/efficient-workflow.md',
    'codex-plugin/skills/chemellm-engineering/references/pricing.md',
    'codex-plugin/skills/chemellm-engineering/references/references.md',
    'docs/blender.md', 'docs/efficiency-v2.md', 'docs/skill-efficiency.md',
    'docs/examples/blender-plant.json', 'release/notes-v0.6.0.md',
    'EXTERNAL-MCP.cmd', 'chemellm_codex/dwsim_mcp.py', 'scripts/setup-external-mcp.py',
    'scripts/freecad-mcp-start.FCMacro', 'scripts/test_external_mcp.py',
    'release/external-mcp.json', 'docs/external-mcp.md', 'release/notes-v0.7.2.md',
}
# Only the five reviewed skill trees; no arbitrary untracked files or user assets.
SKILL_NAMES = {'chemellm-engineering', 'chemellm-dwsim', 'chemellm-3d', 'chemellm-references', 'chemellm-costing'}
SOURCE_ADDITIONS.update('codex-plugin/skills/'+name+'/SKILL.md' for name in SKILL_NAMES)
SOURCE_ADDITIONS.update('codex-plugin/skills/chemellm-dwsim/references/'+name+'.md'
                        for name in ('basics', 'units', 'convergence', 'layout', 'studies', 'publish'))
SOURCE_ADDITIONS.update('codex-plugin/skills/chemellm-dwsim/references/examples/'+name+'.json'
                        for name in ('pump', 'flash', 'heat_exchanger', 'benzene_toluene_column', 'wgs_conversion', 'wgs_equilibrium'))
SOURCE_ADDITIONS.update('codex-plugin/skills/'+name+'/references/'+file
                        for name, file in [('chemellm-3d','blender.md'), ('chemellm-references','references.md'),
                                           ('chemellm-costing','pricing.md')])


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
    tree = ast.parse((ROOT/'chemellm_codex/__init__.py').read_text(encoding='utf-8'))
    runtime = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t,ast.Name) and t.id=='__version__' for t in n.targets))
    if runtime != version:
        raise ValueError('Kernel and package versions differ')
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    names = sorted({name for name in files if name and allowed(name)} | SOURCE_ADDITIONS)
    for name in SOURCE_ADDITIONS:
        if not allowed(name) or not (ROOT/name).is_file():
            raise ValueError('Missing reviewed source: '+name)
    for required in ["INSTALL.cmd", "CHECK.cmd", "scripts/install-windows.ps1", "release/requirements-windows-x64.lock", "安装使用说明.md"]:
        if required not in names:
            raise ValueError(f"Required release file is not tracked: {required}; stage source files first")
    folder = f"ChemELLM-Codex-{version}-windows-x64"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (folder + ".zip")
    skill_sha = skill_fingerprint(ROOT/'codex-plugin/skills/chemellm-engineering')
    manifest = {"version": version, "platform": "windows-x64", "workflowVersion":2,
                "skillFingerprint":skill_sha,
                "skillSetFingerprint":skill_fingerprint(ROOT/'codex-plugin/skills'),
                "skills":skill_inventory(ROOT/'codex-plugin/skills'), "files": []}
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
