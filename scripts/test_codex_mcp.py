"""Real stdio MCP regression with isolated storage and an unrelated working directory.

Run with the installed project interpreter: python -X utf8 scripts/test_codex_mcp.py
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def connected(test):
    @wraps(test)
    async def run(self):
        # AnyIO cancel scopes must enter and exit within the same asyncio task.
        await self.start_session()
        try:
            await test(self)
        except BaseException:
            self.log.flush()
            print((self.folder / "server.log").read_text(encoding="utf-8"), file=sys.stderr)
            raise
        finally:
            await self.close_session()
    return run


class CodexMCPTests(unittest.IsolatedAsyncioTestCase):
    async def start_session(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="chemellm-mcp-tests-")
        self.folder = Path(self.temporary.name)
        self.storage = self.folder / "独立数据 with spaces"
        self.sentinel = self.folder / "must-not-touch"
        self.stack = AsyncExitStack()
        self.log = (self.folder / "server.log").open("w", encoding="utf-8")
        env = {**os.environ, "CHEMELLM_DATA_ROOT": str(self.sentinel),
               "CHEMELLM_KERNEL_DATA": str(self.sentinel), "CHEMELLM_DATA_DIR": str(self.sentinel),
               "CHEMELLM_SNAPSHOT_DIR": str(self.sentinel), "CHEMELLM_APP_DIR": str(self.sentinel),
               "CHEMELLM_KERNEL_TOKEN": "inherited-dsh-token", "CHEMELLM_PHASE_GATE": "advisory"}
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-X", "utf8", "-m", "chemellm_codex", "--workspace", str(ROOT),
                  "--data-root", str(self.storage)],
            cwd=str(self.folder), env=env,
        )
        try:
            reader, writer = await self.stack.enter_async_context(stdio_client(parameters, errlog=self.log))
            self.session = await self.stack.enter_async_context(ClientSession(reader, writer))
            await self.session.initialize()
        except BaseException:
            await self.stack.aclose()
            self.log.close()
            print((self.folder / "server.log").read_text(encoding="utf-8"), file=sys.stderr)
            self.temporary.cleanup()
            raise

    async def close_session(self):
        try:
            await self.stack.aclose()
        finally:
            self.log.close()
            try:
                self.assertFalse(self.sentinel.exists(), "Inherited DSH location was touched")
            finally:
                self.temporary.cleanup()

    async def call(self, tool_name: str, **arguments) -> dict:
        response = await self.session.call_tool(tool_name, arguments)
        self.assertFalse(response.isError, response)
        if response.structuredContent is not None:
            return response.structuredContent
        return json.loads(response.content[0].text)

    @connected
    async def test_full_workflow_resources_and_artifact_hashes(self):
        health = await self.call("chem_health")
        self.assertEqual(Path(health["dataDir"]), self.storage)
        self.assertEqual(health["phaseGate"], "enforce")
        tools = await self.session.list_tools()
        self.assertIn("chem_execute", {tool.name for tool in tools.tools})
        schema = await self.call("chem_tools", tool="properties")
        self.assertIn("compound", schema["schema"]["properties"])
        project = await self.call("chem_create_project", name="Codex 真实协议回归")
        project_id = project["project"]["projectId"]
        await self.call("chem_requirements", project_id=project_id, items=[{
            "code": "R01", "title": "计算水物性并生成报告", "source": "MCP regression",
        }])
        await self.call("chem_plan", project_id=project_id, deliverables=[{
            "key": "report", "title": "物性计算报告", "requirementCodes": ["R01"], "formats": ["md"],
        }])
        await self.call("chem_object", project_id=project_id, kind="basis", key="feed", quantities=[{
            "key": "flow", "value": 100, "unit": "kmol/h", "conditions": "298 K, 101325 Pa",
            "origin": "assumption", "rationale": "协议回归的示例设计依据",
        }])
        calculated = await self.call("chem_execute", project_id=project_id, tool="properties", args={"compound": "water"})
        calculation_id = calculated["calculationId"]
        self.assertTrue(calculation_id)
        full = await self.call("chem_calculations", project_id=project_id, calculation_id=calculation_id)
        self.assertEqual(full["input"]["compound"], "water")
        report = await self.call("chem_execute", project_id=project_id, tool="export_report", args={
            "title": "Codex 回归报告", "body": "用于验证 MCP 适配；不构成专业审核。", "formats": ["md"],
        })
        artifact = report["artifacts"][0]
        path = Path(artifact["localPath"])
        self.assertTrue(path.is_relative_to(self.storage))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact["sha256"])
        opened = await self.call("chem_artifact", artifact_id=artifact["artifactId"])
        self.assertEqual(opened["localPath"], str(path))
        material = self.storage / "原始材料.txt"
        material.write_text("回归用原始材料，不是工程标准。", encoding="utf-8")
        intake = await self.call("chem_intake", project_id=project_id, path=str(material))
        self.assertTrue(Path(intake["artifact"]["localPath"]).is_file())
        reviewed = await self.call("chem_review", project_id=project_id)
        self.assertTrue(reviewed["review"]["ready"], reviewed)
        self.assertEqual(reviewed["review"]["professionalReview"], "not_reviewed")
        bundle = (await self.call("chem_bundle", project_id=project_id))["bundle"]
        bundle_path = Path(bundle["localPath"])
        self.assertEqual(hashlib.sha256(bundle_path.read_bytes()).hexdigest(), bundle["sha256"])
        with zipfile.ZipFile(bundle_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for item in manifest["files"]:
                self.assertEqual(hashlib.sha256(archive.read(item["archivePath"])).hexdigest(), item["sha256"])
            self.assertIn("8766", archive.read("recompute.md").decode("utf-8"))
        resource = await self.session.read_resource(f"chemellm://projects/{project_id}")
        self.assertEqual(json.loads(resource.contents[0].text)["project"]["id"], project_id)
        resource = await self.session.read_resource(f"chemellm://calculations/{project_id}/{calculation_id}")
        self.assertEqual(json.loads(resource.contents[0].text)["calculationId"], calculation_id)
        await self.call("chem_object", project_id=project_id, kind="basis", key="feed", quantities=[{
            "key": "flow", "value": 120, "unit": "kmol/h", "conditions": "298 K, 101325 Pa",
            "origin": "assumption", "rationale": "修订回归",
        }])
        updated = await self.call("chem_project", project_id=project_id)
        self.assertEqual(updated["project"]["review"], {})
        path.unlink()
        bundle_path.unlink()
        recovered = (await self.call("chem_project", project_id=project_id))["project"]
        self.assertTrue(recovered["files"][0]["localFileMissing"])
        self.assertTrue(recovered["bundle"]["localFileMissing"])

    @connected
    async def test_validation_phase_gate_and_import_boundary(self):
        project_id = (await self.call("chem_create_project", name="输入边界"))["project"]["projectId"]
        gated = await self.session.call_tool("chem_execute", {
            "project_id": project_id, "tool": "properties", "args": {"compound": "water"},
        })
        self.assertTrue(gated.isError)
        self.assertIn("409", gated.content[0].text)
        for name, arguments in [
            ("chem_project", {"project_id": "../other"}),
            ("chem_tools", {"tool": "ask_user"}),
            ("chem_artifact", {"artifact_id": "not-an-artifact"}),
            ("chem_create_project", {"name": ""}),
        ]:
            response = await self.session.call_tool(name, arguments)
            self.assertTrue(response.isError, (name, response))
        outside = self.folder / "outside.txt"
        outside.write_text("not in workspace or data", encoding="utf-8")
        rejected = await self.session.call_tool("chem_intake", {"project_id": project_id, "path": str(outside)})
        self.assertTrue(rejected.isError)
        self.assertIn("422", rejected.content[0].text)
        state = await self.call("chem_project", project_id=project_id)
        self.assertEqual(state["project"]["calculations"], [])
        self.assertEqual(state["project"]["files"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
