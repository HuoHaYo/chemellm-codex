"""Stdio entry point. Configure local storage before importing the kernel."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import os
from pathlib import Path
import secrets
import sys


def configure(workspace: Path, data_root: Path | None = None) -> None:
    workspace = workspace.resolve()
    data = data_root.resolve() if data_root else workspace / "data"
    snapshot = data / ".chem" if data_root else workspace / ".chem"
    # Do not inherit DSH data locations or credentials from a parent shell.
    os.environ.update({
        "CHEMELLM_DATA_ROOT": str(data),
        "CHEMELLM_KERNEL_DATA": str(data / "kernel"),
        "CHEMELLM_DATA_DIR": str(data / "kernel"),
        "CHEMELLM_SNAPSHOT_DIR": str(snapshot),
        "CHEMELLM_APP_DIR": str(Path(__file__).resolve().parents[1] / "app"),
        "CHEMELLM_KERNEL_HOST": "127.0.0.1",
        "CHEMELLM_KERNEL_PORT": "8766",
        "CHEMELLM_KERNEL_TOKEN": secrets.token_urlsafe(32),
        "CHEMELLM_PHASE_GATE": "enforce",
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="ChemELLM Codex MCP server (stdio)")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(),
                        help="Local workspace for data/ and .chem/ (default: current directory)")
    parser.add_argument("--data-root", type=Path,
                        help="Explicit isolated storage, primarily for regression tests")
    options = parser.parse_args()
    configure(options.workspace, options.data_root)
    # Scientific libraries may print at import time; stdout belongs to MCP only.
    with redirect_stdout(sys.stderr):
        from chemellm_codex.server import create_server
        server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
