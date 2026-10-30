"""Check the generated launcher with a real MCP subprocess and temporary storage."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def check(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["chemellm"]
    with tempfile.TemporaryDirectory(prefix="chemellm-install-check-") as temporary:
        parameters = StdioServerParameters(
            command=config["command"], args=[*config["args"], "--data-root", temporary],
            cwd=temporary,
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                response = await session.call_tool("chem_health", {})
                if response.isError:
                    raise RuntimeError(str(response.content))
                health = response.structuredContent or json.loads(response.content[0].text)
                assert Path(health["dataDir"]).resolve() == Path(temporary).resolve(), health
                assert health["phaseGate"] == "enforce", health
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"chem_execute", "chem_project", "chem_review", "chem_bundle"} <= names
                print(f"MCP startup OK; {len(names)} tools found; isolated storage and phase gate OK.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "codex-plugin" / ".mcp.json")
    options = parser.parse_args()
    asyncio.run(check(options.config))


if __name__ == "__main__":
    main()
