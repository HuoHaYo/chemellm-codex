"""Exercise distributable optional MCP setup in an isolated installation."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("external_setup", ROOT / "scripts/setup-external-mcp.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ExternalMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chemellm mcp 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_config_merge_and_disable_preserve_existing_data(self):
        config = self.root / "plugin/.mcp.json"
        config.parent.mkdir()
        original = {"custom": {"command": "custom.exe", "args": []},
                    "chemellm": {"args": ["--data-root", "D:/projects", "--allowed-root", "D:/inputs"]}}
        config.write_text(json.dumps({"mcpServers": original}), encoding="utf-8")
        server = {"command": sys.executable, "args": ["-m", "example"]}
        with patch.object(setup, "CONFIG", config):
            result = setup.update_config({"chemellm-dwsim": server})
            assert result["chemellm-dwsim"] == server and result["custom"] == original["custom"]
            assert str(ROOT) in result["chemellm"]["args"]
            for argument in ("D:/projects", "D:/inputs"):
                assert argument in result["chemellm"]["args"]
            config_after_setup = json.loads(config.read_text(encoding="utf-8"))
            assert config_after_setup["mcpServers"]["chemellm-dwsim"] == server
            assert "chemellm-dwsim" not in setup.update_config(disable="dwsim")

    def test_checksum_and_archive_boundary_fail_closed(self):
        staged = self.root / "untrusted.zip"
        with zipfile.ZipFile(staged, "w") as archive:
            archive.writestr("package/main.py", "pass")
            archive.writestr("package/../../bad.py", "pass")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            setup.fetch({"sha256": "f" * 64}, self.root / "copied.zip", staged)
        with self.assertRaisesRegex(ValueError, "Invalid asset member"):
            setup.unpack(staged, self.root / "destination", "main.py", strip_root=True)
        assert not (self.root / "bad.py").exists() and not (self.root / "destination").exists()

    def test_standalone_dwsim_discovery_and_real_engine_when_available(self):
        from kernel import dwsim_bridge
        exchange = self.root / "交换区"
        async def verify():
            params = StdioServerParameters(command=sys.executable, cwd=self.root,
                args=["-X", "utf8", "-m", "chemellm_codex.dwsim_mcp", "--exchange", str(exchange)])
            async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert tools == {"dwsim_engine_status", "dwsim_catalog", "dwsim_preflight",
                                 "dwsim_inspect", "dwsim_create", "dwsim_solve"}
                ready = dwsim_bridge.status()["ready"]
                def body(response):
                    assert not response.isError, response.content
                    return response.structuredContent or json.loads(response.content[0].text)
                status = body(await session.call_tool("dwsim_engine_status", {}))
                assert status["ready"] == ready
                rejected = await session.call_tool("dwsim_inspect", {"filename": "../outside.dwxmz"})
                assert rejected.isError
                if not ready:
                    print("  DWSIM engine absent; only protocol and rejection exercised.")
                    return
                catalog = body(await session.call_tool("dwsim_catalog", {"query": "water", "limit": 2}))
                assert any(item["name"] == "Water" for item in catalog["compounds"])
                model = {"compounds": ["Water"], "property_package": "Steam Tables (IAPWS-IF97)",
                    "objects": [
                        {"tag": "COLD", "type": "MaterialStream", "temperature_k": 300, "pressure_pa": 200000,
                         "mass_flow_kg_s": 2, "mole_fractions": {"Water": 1}},
                        {"tag": "HOT", "type": "MaterialStream", "temperature_k": 350, "pressure_pa": 200000,
                         "mass_flow_kg_s": 1, "mole_fractions": {"Water": 1}},
                        {"tag": "MIX", "type": "Mixer"},
                        {"tag": "OUT", "type": "MaterialStream"},
                    ], "connections": [{"source": "COLD", "target": "MIX"},
                                       {"source": "HOT", "target": "MIX", "target_port": 1},
                                       {"source": "MIX", "target": "OUT"}]}
                generated = body(await session.call_tool("dwsim_create", {"model": model}))
                assert generated["solved"] and generated["validation_passed"], generated
                source = exchange / "源模型.dwxmz"
                initial = Path(generated["model"]).read_bytes()
                source.write_bytes(initial)
                checked = body(await session.call_tool("dwsim_preflight", {"filename": source.name}))
                assert checked["preflight"]
                solved = body(await session.call_tool("dwsim_solve", {"filename": source.name,
                    "updates": [{"tag": "COLD", "type": "MaterialStream", "temperature_k": 320}]}))
                assert solved["solved"] and solved["validation_passed"], solved
                assert abs(next(s["temperature_k"] for s in solved["streams"] if s["tag"] == "COLD") - 320) < .001
                assert source.read_bytes() == initial
        asyncio.run(asyncio.wait_for(verify(), timeout=240))


if __name__ == "__main__":
    unittest.main(verbosity=2)
