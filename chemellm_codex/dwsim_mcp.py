"""Standalone steady-state MCP for the existing Windows Automation3 engine."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from app.dwsim import DwsimModel, DwsimObject

Timeout = Annotated[int, Field(ge=5, le=180)]


def create_server(exchange: Path):
    from app.dwsim import DwsimInput
    from app.dwsim_validation import model_preflight, preflight, validate
    from kernel import dwsim_bridge as bridge

    exchange = exchange.resolve()
    exchange.mkdir(parents=True, exist_ok=True)
    mcp = FastMCP("ChemELLM DWSIM Automation3")

    @mcp.tool()
    def dwsim_engine_status() -> dict:
        """Check the installed Windows DWSIM engine and supported steady-state scope."""
        status = bridge.status()
        return {key: status[key] for key in ("installed", "ready", "scope", "actions")}

    @mcp.tool()
    def dwsim_catalog(query: str = "", offset: int = 0, limit: Annotated[int, Field(ge=1, le=100)] = 30) -> dict:
        """Find compounds, property packages and unit operations in the installed engine."""
        if len(query) > 200 or offset < 0:
            raise ValueError("Invalid query or offset.")
        with tempfile.TemporaryDirectory(prefix="chemellm-dwsim-") as work:
            return bridge.catalog(Path(work), query=query, offset=offset, limit=limit)

    def execute(action, timeout, filename=None, model=None, updates=None):
        if not bridge.status()["ready"]:
            raise RuntimeError("Install the supported Windows DWSIM engine or set DWSIM_HOME.")
        source = None
        if filename is not None:
            name = Path(filename)
            if not filename or name.name != filename or name.is_absolute() or ":" in filename:
                raise ValueError("Use a model filename staged in the exchange folder.")
            source = (exchange / name).resolve()
            if source.parent != exchange:
                raise ValueError("Model path leaves the exchange folder.")
            bridge.validate_flowsheet(source)

        def run(folder: Path, keep):
            working = None
            source_sha256 = None
            if source is not None:
                working = folder / ("input" + source.suffix.lower())
                shutil.copyfile(source, working)
                source_sha256 = hashlib.sha256(working.read_bytes()).hexdigest()
                bridge.validate_flowsheet(working)
            request = DwsimInput(action=action, flowsheet_path=str(working) if working else None,
                                 model=model, updates=updates or [], timeout_seconds=timeout)
            if action == "create":
                violations = [item for item in model_preflight(model) if item["passed"] is False]
                if violations:
                    raise ValueError(f"DWSIM preflight failed: {violations}")
            target = folder / "solved.dwxmz" if keep else None
            result = bridge.invoke(request, folder / "work", save_path=target)
            if action == "inspect":
                return {"source_sha256": source_sha256, "objects": result.get("objects", []),
                        "streams": result.get("streams", []), "components": result.get("components", [])}
            if action == "check":
                return {"source_sha256": source_sha256, "preflight": preflight(result)}
            checks = validate(result, request.validation_options)
            summary = {"source_sha256": source_sha256, "solved": result.get("solved") is True,
                       "validation_passed": checks["passed"] is True, "streams": result.get("streams", []),
                       "solver_errors": result.get("solver_errors", [])}
            if target.is_file() and summary["solved"] and summary["validation_passed"]:
                bridge.validate_flowsheet(target)
                summary.update(model=str(target), model_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
            record = folder / "result.json"
            record.write_text(json.dumps({**summary, "validation": checks}, ensure_ascii=False, indent=2),
                              encoding="utf-8")
            return {**summary, "record": str(record)}

        if action in {"run", "create"}:
            runs = exchange / "runs"
            if runs.resolve().parent != exchange or runs.is_symlink():
                raise ValueError("Output folder leaves the exchange directory.")
            runs.mkdir(exist_ok=True)
            return run(Path(tempfile.mkdtemp(prefix="solve-", dir=runs)), True)
        with tempfile.TemporaryDirectory(prefix="chemellm-dwsim-") as work:
            return run(Path(work), False)

    @mcp.tool()
    def dwsim_inspect(filename: str, timeout_seconds: Timeout = 60) -> dict:
        """Read a trusted .dwxmz or .dwxml from the exchange folder without solving."""
        return execute("inspect", timeout_seconds, filename)

    @mcp.tool()
    def dwsim_preflight(filename: str, timeout_seconds: Timeout = 60,
                        updates: Annotated[list[DwsimObject] | None, Field(max_length=200)] = None) -> dict:
        """Check an existing model and optional proposed operating changes without saving it."""
        return execute("check", timeout_seconds, filename, updates=updates)

    @mcp.tool()
    def dwsim_solve(filename: str, timeout_seconds: Timeout = 120,
                    updates: Annotated[list[DwsimObject] | None, Field(max_length=200)] = None) -> dict:
        """Solve at steady state with optional operating changes. Keep the original model unchanged."""
        return execute("run", timeout_seconds, filename, updates=updates)

    @mcp.tool()
    def dwsim_create(model: DwsimModel, timeout_seconds: Timeout = 120) -> dict:
        """Create and solve a new explicitly specified steady-state model."""
        return execute("create", timeout_seconds, model=model)

    return mcp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", required=True, type=Path)
    args = parser.parse_args()
    with redirect_stdout(sys.stderr):
        server = create_server(args.exchange)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
