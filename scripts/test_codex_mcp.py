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

    @connected
    async def test_price_book_real_protocol_manual_edit_and_history(self):
        available = {tool.name for tool in (await self.session.list_tools()).tools}
        self.assertTrue({'chem_prices', 'chem_price_save', 'chem_price_history'} <= available)
        project_id = (await self.call('chem_create_project', name='本地报价台账'))['project']['projectId']
        current = (await self.call('chem_prices', project_id=project_id))['prices']
        self.assertEqual(current['revision'], 0)
        self.assertTrue(Path(current['files']['current']).is_relative_to(self.storage))
        price = {
            'key': 'equipment-P-101', 'category': 'equipment', 'title': '测试泵', 'equipment_tag': 'P-101',
            'quantity': 1, 'quantity_unit': '台', 'unit_price': 10000, 'currency': 'CNY',
            'cost_basis': 'one_time', 'conditions': '测试工况，非真实项目数据',
            'origin': 'assumption', 'source': '测试暂估依据', 'rationale': '验证假设和缺项提示',
        }
        # Price registration is available before the process-design phase gate.
        saved = (await self.call('chem_price_save', project_id=project_id, items=[price],
                                 expected_revision=0, reason='回归测试补缺项', mode='fill_missing'))['prices']
        self.assertEqual(saved['revision'], 1)
        self.assertTrue(saved['pending_review'])
        price.update(origin='quotation', unit_price=12000, source='测试用户原话：单台 12000 元',
                     quote_date='2026-01-01', valid_until='2099-01-01')
        saved = (await self.call('chem_price_save', project_id=project_id, items=[price],
                                 expected_revision=1, reason='用户提供测试价格'))['prices']
        self.assertEqual(saved['items'][0]['origin'], 'quotation')
        path = Path(saved['files']['current'])
        document = json.loads(path.read_text(encoding='utf-8'))
        document['items'][0]['unit_price'] = 12500
        path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        # Resource reads follow the same reconciliation path as HTTP and tools.
        resource = await self.session.read_resource(f'chemellm://projects/{project_id}')
        project = json.loads(resource.contents[0].text)['project']
        self.assertEqual(project['prices']['revision'], 3)
        self.assertEqual(project['prices']['items'][0]['unit_price'], 12500)
        history = await self.call('chem_price_history', project_id=project_id, key=price['key'], offset=1, limit=2)
        self.assertEqual(history['total'], 3)
        self.assertEqual(history['events'][0]['changes'][0]['before']['origin'], 'assumption')
        self.assertEqual(history['events'][1]['actor'], 'local_edit')
        rejected = await self.session.call_tool('chem_price_save', {
            'project_id': project_id, 'items': [price], 'expected_revision': 2, 'reason': '过期版本',
        })
        self.assertTrue(rejected.isError)
        self.assertIn('409', rejected.content[0].text)
        rejected = await self.session.call_tool('chem_price_save', {
            'project_id': project_id, 'items': [{**price, 'origin': 'assumption'}],
            'expected_revision': 3, 'reason': '禁止假设覆盖实价',
        })
        self.assertTrue(rejected.isError)
        self.assertEqual((await self.call('chem_prices', project_id=project_id))['prices']['revision'], 3)

    @connected
    async def test_shared_references_text_images_history_and_pinned_use(self):
        import base64
        from PIL import Image, ImageDraw
        names = {tool.name for tool in (await self.session.list_tools()).tools}
        self.assertTrue({'chem_reference_import', 'chem_reference_read', 'chem_reference_save',
                         'chem_reference_search', 'chem_reference_use'} <= names)
        source = {'title': '测试设计参考条款', 'source': '用户提供的合成测试材料', 'notes': '非实际标准'}
        raw = '合成测试条款：设计温度限值为 160 degC，使用时核对材料与适用工况。'
        imported = (await self.call('chem_reference_import', source=source, text=raw))['reference']
        ident = imported['reference_id']
        self.assertTrue(Path(imported['files']['original']).is_relative_to(self.storage/'references'))
        self.assertEqual(Path(imported['files']['original']).read_text(encoding='utf-8'), raw)
        self.assertTrue((await self.call('chem_reference_import', source=source, text=raw))['reference']['duplicate'])
        read = (await self.call('chem_reference_read', reference_id=ident))['reference']
        block_id = read['items'][0]['id']
        saved = (await self.call('chem_reference_save', reference_id=ident, expected_revision=1,
                                reason='当前会话整理', overview='设计温度索引', entries=[{
                                    'key': 'temperature', 'text': '保留温度限值与适用条件。',
                                    'source_block_ids': [block_id]}]))['reference']
        self.assertEqual(saved['revision'], 2)
        source_path = Path(imported['files']['source'])
        changed = {**source, 'notes': '用户备注：题设附录节选'}
        source_path.write_text(json.dumps(changed, ensure_ascii=False), encoding='utf-8')
        searched = await self.call('chem_reference_search', query='设计温度')
        self.assertTrue(any(item['reference_id'] == ident and item['revision'] == 3 for item in searched['results']), searched)
        old = (await self.call('chem_reference_read', reference_id=ident, revision=2, view='metadata'))['reference']
        self.assertEqual(old['source']['notes'], '非实际标准')
        stale = await self.session.call_tool('chem_reference_save', {
            'reference_id': ident, 'expected_revision': 2, 'reason': '过期编辑', 'overview': '旧概览'})
        self.assertTrue(stale.isError)
        self.assertIn('409', stale.content[0].text)
        project_id = (await self.call('chem_create_project', name='共享资料协议回归'))['project']['projectId']
        pinned = await self.call('chem_reference_use', project_id=project_id, reference_id=ident,
                                 revision=2, block_ids=[block_id], purpose='回归引用原文')
        snapshot = await self.call('chem_execute', project_id=project_id, tool='document_read',
                                  args={'snapshot_id': pinned['snapshot_id']})
        # document_read results are wrapped by the normal execution contract.
        self.assertIn('160 degC', json.dumps(snapshot, ensure_ascii=False))
        state = (await self.call('chem_project', project_id=project_id))['project']
        self.assertEqual(state['references'][0]['revision'], 2)
        bundle = (await self.call('chem_bundle', project_id=project_id))['bundle']
        with zipfile.ZipFile(bundle['localPath']) as archive:
            self.assertEqual(json.loads(archive.read('references.json'))[0]['revision'], 2)
            for item in json.loads(archive.read('manifest.json'))['files']:
                self.assertEqual(hashlib.sha256(archive.read(item['archivePath'])).hexdigest(), item['sha256'])
        image_path = self.storage/'测试资料图像.png'
        with Image.new('RGB', (450, 140), 'white') as image:
            ImageDraw.Draw(image).text((15, 40), 'Test limit 160 degC', fill='black')
            image.save(image_path)
        image_ref = (await self.call('chem_reference_import', source=source, path=str(image_path)))['reference']
        rendered = await self.session.call_tool('chem_reference_read', {
            'reference_id': image_ref['reference_id'], 'view': 'image', 'page': 1})
        self.assertFalse(rendered.isError, rendered)
        images = [item for item in rendered.content if item.type == 'image']
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mimeType, 'image/png')
        self.assertTrue(base64.b64decode(images[0].data).startswith(b'\x89PNG'))
        anchor = rendered.structuredContent['reference']['source_block_ids'][0]
        await self.call('chem_reference_save', reference_id=image_ref['reference_id'], expected_revision=1,
                        reason='当前会话逐字识读测试', entries=[{
                            'key': 'visible', 'kind': 'transcription', 'text': 'Test limit 160 degC', 'source_block_ids': [anchor]}])
        transcribed = await self.call('chem_reference_use', project_id=project_id, reference_id=image_ref['reference_id'],
                                     revision=2, block_ids=['t-visible'], purpose='图像引用回归')
        self.assertTrue(transcribed['citations'][0]['ocr'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
