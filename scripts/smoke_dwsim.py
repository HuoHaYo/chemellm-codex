"""Smoke test for the DWSIM bridge.

Runs DWSIM's own Cavett sample through the same bridge used by the kernel.
Requires DWSIM installed locally; DWSIM is GPL external software and is not
bundled with the plugin.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "kernel"))

import dwsim_bridge  # noqa: E402


def main() -> int:
    status = dwsim_bridge.status()
    print("DWSIM:", status)
    if not status["installed"]:
        print("SKIP: DWSIM not installed")
        return 0
    home = Path(status["home"])
    sample = home / "samples" / "Cavett's Problem.dwxmz"
    if not sample.is_file():
        print(f"SKIP: sample not found: {sample}")
        return 0
    result = dwsim_bridge.run_flowsheet(sample, ROOT / "data" / "qa" / "dwsim-smoke")
    print("version:", result.get("version"))
    print("solved:", result.get("solved"))
    print("streams:", len(result.get("streams") or []))
    print("units:", len(result.get("unit_operations") or []))
    if not result.get("solved"):
        print("FAIL: DWSIM did not solve")
        return 1
    if not result.get("streams"):
        print("FAIL: no material streams")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
