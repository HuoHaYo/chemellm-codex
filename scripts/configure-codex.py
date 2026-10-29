"""Generate machine-local plugin MCP config; do not edit the user's Codex settings."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "codex-plugin" / ".mcp.json")
    options = parser.parse_args()
    if importlib.util.find_spec("chemellm_codex") is None or importlib.util.find_spec("mcp") is None:
        parser.error('Install this project first: python -m pip install -e ".[dev]"')
    config = {"mcpServers": {"chemellm": {
        "command": str(Path(sys.executable).resolve()),
        "args": ["-X", "utf8", "-m", "chemellm_codex", "--workspace", str(ROOT)],
        "startup_timeout_sec": 60,
        "tool_timeout_sec": 600,
    }}}
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Generated:", options.output)
    print("Python:", sys.executable)
    print("Workspace:", ROOT)
    print("Next: codex plugin marketplace add", str(ROOT))
    print("Then: codex plugin add chemellm@chemellm-local")


if __name__ == "__main__":
    main()
