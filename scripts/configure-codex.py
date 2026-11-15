"""Generate machine-local plugin MCP config; do not edit the user's Codex settings."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def previous_values(arguments, flag):
    values = []
    for i, arg in enumerate(arguments):
        if arg == flag and i+1 < len(arguments):
            values.append(arguments[i+1])
        elif arg.startswith(flag+'='):
            values.append(arg.split('=', 1)[1])
    return values


def generate(output: Path, data_root: Path | None = None, allowed_roots: list[Path] | None = None):
    from chemellm_codex import skill_fingerprint
    previous = json.loads(output.read_text(encoding='utf-8')) if output.exists() else {}
    old_entry = previous.get('mcpServers', {}).get('chemellm', {})
    arguments = old_entry.get('args', [])
    old_data = previous_values(arguments, '--data-root')
    storage = str(data_root.resolve()) if data_root is not None else (old_data[-1] if old_data else None)
    allowed = ([str(p.resolve()) for p in allowed_roots] if allowed_roots is not None else
               previous_values(arguments, '--allowed-root'))
    entry = {**old_entry, 'command': str(Path(sys.executable).resolve()),
             'args': ['-X', 'utf8', '-m', 'chemellm_codex', '--workspace', str(ROOT),
                      '--skill-fingerprint', skill_fingerprint(ROOT/'codex-plugin/skills/chemellm-engineering'),
                      '--skill-set-fingerprint', skill_fingerprint(ROOT/'codex-plugin/skills'),
                      *(['--data-root', storage] if storage else []),
                      *[arg for root in dict.fromkeys(allowed) for arg in ('--allowed-root', root)]],
             'startup_timeout_sec': old_entry.get('startup_timeout_sec', 60),
             'tool_timeout_sec': old_entry.get('tool_timeout_sec', 600)}
    previous.setdefault('mcpServers', {})['chemellm'] = entry
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(previous, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "codex-plugin" / ".mcp.json")
    parser.add_argument('--allowed-root', type=Path, action='append', default=None)
    parser.add_argument('--data-root', type=Path, help='Isolated project storage; when omitted preserve the existing configuration')
    options = parser.parse_args()
    if importlib.util.find_spec("chemellm_codex") is None or importlib.util.find_spec("mcp") is None:
        parser.error('Install this project first: python -m pip install -e ".[dev]"')
    generate(options.output, options.data_root, options.allowed_root)
    print("Generated:", options.output)
    print("Python:", sys.executable)
    print("Workspace:", ROOT)
    print("Next: codex plugin marketplace add", str(ROOT))
    print("Then: codex plugin add chemellm@chemellm-local")


if __name__ == "__main__":
    main()
