"""MCP tools backed by the existing HTTP contract, without opening a socket."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
from typing import Literal
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from mcp.types import CallToolResult, TextContent
from mcp.server.fastmcp.utilities.types import Image as MCPImage

from kernel import server as kernel
from app.pricing import PriceItem
from app.references import ReferenceSource, ReferenceEntry


def segment(value: str) -> str:
    if not value or value in {".", ".."} or any(c in value for c in "/\\\x00"):
        raise ToolError("标识符不能为空或包含路径分隔符")
    return quote(value, safe="")


def local_file(path: Path) -> dict:
    path = path.resolve()
    return {
        "localPath": str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class KernelBridge:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.client: httpx.AsyncClient | None = None

    async def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if self.client is None:
            raise ToolError("内核适配器尚未启动")
        # The kernel writes JSON project state; serialize calls in this process.
        async with self.lock:
            with redirect_stdout(sys.stderr):
                response = await self.client.request(method, path, json=payload)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ToolError(f"内核 HTTP {response.status_code}: {detail}")
        result = response.json()
        self.attach_paths(result)
        return result

    def attach_paths(self, value: object) -> None:
        if isinstance(value, dict):
            artifact_id = value.get("artifactId")
            if artifact_id and value.get("downloadPath"):
                try:
                    value.update(local_file(kernel._artifact_path(str(artifact_id))))
                except (kernel.HTTPException, OSError):
                    # Missing originals must not hide the project needed for recovery.
                    value["localFileMissing"] = True
            bundle = value.get("bundle")
            if isinstance(bundle, dict) and bundle.get("name"):
                name = str(bundle["name"])
                segment(name)
                try:
                    bundle.update(local_file(kernel.BUNDLES_DIR / name))
                except OSError:
                    bundle["localFileMissing"] = True
            for child in list(value.values()):
                self.attach_paths(child)
        elif isinstance(value, list):
            for child in value:
                self.attach_paths(child)


def create_server() -> FastMCP:
    bridge = KernelBridge()

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=kernel.app),
            base_url="http://127.0.0.1:8766",
            headers={"x-kernel-token": kernel.KERNEL_TOKEN},
            timeout=None,
        ) as client:
            bridge.client = client
            try:
                yield bridge
            finally:
                bridge.client = None

    server = FastMCP(
        "chemellm",
        instructions=("化工计算和工程成果使用本地项目、真实工具结果及可追溯依据。"
                      "先读项目和工具参数；数值保留单位、条件、来源。"
                      "收到真实价格后用 chem_price_save 持久化；先 chem_prices 读取版本。缺价估值须标为 assumption 并说明依据，不得覆盖真实价格。"
                      "每次新增或更新价格填写本次 source，并在 notes 备注题设假定、工厂报价或 AI 暂估及适用背景；引用时结合备注判断。"
                      "设计资料使用 chem_reference_* 自动归档到共享库；当前会话读取原文/图像后保存带定位的整理稿，引用固定版本并回看原文。"
                      "自动校核不能替代专业审核。失败后先核对项目状态再重试。"),
        lifespan=lifespan,
        log_level="WARNING",
    )
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    def project_path(project_id: str) -> str:
        return "/v1/projects/" + segment(project_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def chem_reference_import(source: ReferenceSource, path: str | None = None,
                                    text: str | None = None, url: str | None = None,
                                    name: str | None = None) -> dict:
        """归档文件、粘贴原文或公开网址（三选一）到跨项目共享库；保留原件、来源、全文和处理进度。

        不调用模型 API。后续由当前 Codex 会话读取、识图并保存整理稿。文件须在本仓库或数据目录，最多25 MiB。
        """
        return await bridge.request('POST', '/v1/references/import', {
            'source': source.model_dump(), 'path': path, 'text': text, 'url': url, 'name': name})

    @server.tool(annotations=write)
    async def chem_reference_read(reference_id: str, revision: int | None = None,
                                  view: Literal['metadata', 'source', 'organized', 'image'] = 'source',
                                  page: int | None = None, block_ids: list[str] | None = None,
                                  offset: int = 0, limit: int = 10) -> CallToolResult:
        """分页读原文、整理稿或历史版本；view=image 将指定页直接作为图像返回给当前 Codex 识读。

        source 的 b... 是原文定位；organized 的 t-... 是待核对逐字转录，s-... 是不能冒充原文的整理稿。
        读取当前版本会记录本地文件编辑；next_offset 表示尚有内容。混合 PDF 页也可读取图像。
        """
        from urllib.parse import urlencode
        query = {'view': view, 'offset': offset, 'limit': limit}
        if revision is not None:
            query['revision'] = revision
        if page is not None:
            query['page'] = page
        if block_ids:
            query['block_ids'] = ','.join(block_ids)
        result = await bridge.request('GET', '/v1/references/'+segment(reference_id)+'?'+urlencode(query))
        content = [TextContent(type='text', text=json.dumps(result, ensure_ascii=False))]
        rendered = result['reference'].get('image')
        if rendered:
            path = Path(rendered['localPath']).resolve()
            if not path.is_relative_to(kernel.REFERENCE_LIBRARY.root):
                raise ToolError('资料图像路径越界')
            content.append(MCPImage(path=path).to_image_content())
        return CallToolResult(content=content, structuredContent=result)

    @server.tool(annotations=write)
    async def chem_reference_save(reference_id: str, expected_revision: int, reason: str,
                                  source: ReferenceSource | None = None, overview: str | None = None,
                                  entries: list[ReferenceEntry] | None = None,
                                  remove_keys: list[str] | None = None, brief: str | None = None) -> dict:
        """保存当前会话的来源备注、概览和条目，按 key 合并、保留历史；先读当前版本以免覆盖手工编辑。

        每条关联 source_block_ids。图片逐字识读用 transcription，精简用 summary/table/formula/figure；不清处标[不清]。
        不改动原件或原文层；长资料可分批整理、以后补充。
        """
        return await bridge.request('POST', '/v1/references/'+segment(reference_id), {
            'expected_revision': expected_revision, 'reason': reason,
            'source': source.model_dump() if source else None, 'overview': overview,
            'entries': [entry.model_dump() for entry in entries or []],
            'remove_keys': remove_keys or [], 'brief': brief})

    @server.tool(annotations=write)
    async def chem_reference_search(query: str = '', offset: int = 0, limit: int = 10) -> dict:
        """检索共享库的来源备注、原文及整理稿，返回版本和定位；空 query 分页列出资料。引用前回看原文。"""
        from urllib.parse import urlencode
        return await bridge.request('GET', '/v1/references?'+urlencode({'query': query, 'offset': offset, 'limit': limit}))

    @server.tool(annotations=write)
    async def chem_reference_use(project_id: str, reference_id: str, revision: int,
                                 block_ids: list[str], purpose: str) -> dict:
        """把原文/逐字转录的固定版本复制到项目，返回可供 evidence_add 使用的 snapshot_id/block_id。

        项目副本不随共享库更新，原件、来源和整理稿随项目打包；删除项目不会删除共享库。
        """
        return await bridge.request('POST', project_path(project_id)+'/references', {
            'reference_id': reference_id, 'revision': revision, 'block_ids': block_ids, 'purpose': purpose})

    @server.tool(annotations=read)
    async def chem_health() -> dict:
        """读取本地内核状态、数据路径和阶段约束。MCP 不需要另开 HTTP 服务。"""
        return await bridge.request("GET", "/v1/health")

    @server.tool(annotations=read)
    async def chem_tools(tool: str | None = None) -> dict:
        """列出可执行工具及 JSON 参数 schema；指定 tool 可只查一个工具。"""
        catalog = await bridge.request("GET", "/v1/tools")
        if tool is None:
            return catalog
        if tool not in catalog["executable"]:
            raise ToolError("未知或不可执行的工具：" + tool)
        return {
            "tool": tool,
            "schema": catalog["schemas"].get(tool),
            "description": next((item for item in catalog["tools"] if item["id"] == tool), None),
            "specialTool": tool in kernel.SPECIAL_TOOLS,
            "note": "specialTool 无 schema 时请查阅对应 kernel 模块；不要猜测必需参数。",
        }

    @server.tool(annotations=read)
    async def chem_projects() -> dict:
        """列出本工作区的本地工程项目。"""
        return await bridge.request("GET", "/v1/projects")

    @server.tool(annotations=write)
    async def chem_create_project(name: str) -> dict:
        """建立本地项目，返回 projectId。"""
        return await bridge.request("POST", "/v1/projects", {"name": name})

    @server.tool(annotations=read)
    async def chem_project(project_id: str) -> dict:
        """读取要求、计划、工程对象、计算、文件和审核状态。"""
        return await bridge.request("GET", project_path(project_id))

    @server.tool(annotations=write)
    async def chem_prices(project_id: str) -> dict:
        """读取当前价格、版本、待核对项及可编辑本地文件路径；自动把手工增删改保存为历史版本。"""
        return await bridge.request('GET', project_path(project_id) + '/prices')

    @server.tool(annotations=write)
    async def chem_price_save(project_id: str, items: list[PriceItem], expected_revision: int,
                              reason: str, mode: Literal['upsert', 'fill_missing'] = 'upsert') -> dict:
        """按稳定 key 更新设备和其他费用，保留旧值历史。先读 chem_prices，再给出当前版本和完整行。

        origin=quotation 表示真实价格，assumption 表示有估算依据的假设。
        fill_missing 只补充缺项假设；真实报价使用 upsert。未知元数据留空并返回待核对项。
        每次更新填写本次价格的 source；notes 备注题设假定、工厂报价或 AI 暂估及适用背景。
        """
        return await bridge.request('POST', project_path(project_id) + '/prices', {
            'items': [item.model_dump(mode='json') for item in items],
            'expected_revision': expected_revision, 'reason': reason, 'mode': mode,
        })

    @server.tool(annotations=write)
    async def chem_price_history(project_id: str, key: str | None = None,
                                 offset: int = 0, limit: int = 50) -> dict:
        """读取价格历次增删改的时间、操作者、理由与前后完整数值；可按 key 筛选，自动导入本地编辑。"""
        from urllib.parse import urlencode
        query = {'offset': offset, 'limit': limit}
        if key is not None:
            query['key'] = key
        return await bridge.request('GET', project_path(project_id) + '/prices/history?' + urlencode(query))

    @server.tool(annotations=write)
    async def chem_requirements(project_id: str, items: list[kernel.RequirementItem]) -> dict:
        """保存要求清单；source/quote 保留用户原文或可定位依据。"""
        return await bridge.request("POST", project_path(project_id) + "/requirements",
                                    {"items": [item.model_dump() for item in items]})

    @server.tool(annotations=write)
    async def chem_plan(project_id: str, objects: list[kernel.PlanObject] | None = None,
                        deliverables: list[kernel.PlanDeliverable] | None = None) -> dict:
        """保存工程对象和成果计划，把 requirementCodes 映射到实际文件格式。"""
        return await bridge.request("POST", project_path(project_id) + "/plan", {
            "objects": [item.model_dump() for item in objects or []],
            "deliverables": [item.model_dump() for item in deliverables or []],
        })

    @server.tool(annotations=write)
    async def chem_object(project_id: str, kind: str, key: str, title: str = "",
                          quantities: list[dict] | None = None,
                          attributes: dict | None = None, depends_on: list[str] | None = None) -> dict:
        """保存工程对象。quantities 每项保留 key/value/unit/conditions/origin/rationale。"""
        return await bridge.request("POST", project_path(project_id) + "/objects", {
            "kind": kind, "key": key, "title": title, "quantities": quantities or [],
            "attributes": attributes or {}, "dependsOn": depends_on or [],
        })

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def chem_execute(project_id: str, tool: str, args: dict) -> dict:
        """运行目录中的计算、证据或生成工具。先 chem_tools 查参数；成果返回本地路径和哈希。

        错误或超时可能已有写入，重试前用 chem_project/chem_calculations 核对。
        """
        return await bridge.request("POST", project_path(project_id) + "/execute", {"tool": tool, "args": args})

    @server.tool(annotations=read)
    async def chem_calculations(project_id: str, calculation_id: str | None = None) -> dict:
        """列出计算记录，或按 calculation_id 读取完整输入/结果，避免使用截断数值。"""
        path = project_path(project_id) + "/calculations"
        if calculation_id is not None:
            path += "/" + segment(calculation_id)
        return await bridge.request("GET", path)

    @server.tool(annotations=write)
    async def chem_intake(project_id: str, path: str, name: str | None = None) -> dict:
        """导入本仓库或数据目录内的材料（最多 25 MiB），保留文件哈希。"""
        return await bridge.request("POST", project_path(project_id) + "/intake", {"path": path, "name": name})

    @server.tool(annotations=read)
    async def chem_artifact(artifact_id: str, preview: bool = False) -> dict:
        """读取成果文件的本地绝对路径、字节数和 SHA256，可直接在 Codex 打开。"""
        segment(artifact_id)
        try:
            path = kernel._artifact_path(artifact_id)
            if preview:
                row = kernel.one("SELECT preview_path FROM artifacts WHERE id=?", (artifact_id,))
                if row and row["preview_path"]:
                    candidate = Path(row["preview_path"]).resolve()
                    if candidate.is_file() and candidate.is_relative_to(kernel.KERNEL_DATA.resolve()):
                        path = candidate
            return {"artifactId": artifact_id, **local_file(path)}
        except kernel.HTTPException as exc:
            raise ToolError(str(exc.detail)) from exc

    @server.tool(annotations=write)
    async def chem_review(project_id: str, note: str = "") -> dict:
        """执行自动完整性校核，返回缺口；professionalReview 始终独立。"""
        return await bridge.request("POST", project_path(project_id) + "/review", {"note": note})

    @server.tool(annotations=write)
    async def chem_bundle(project_id: str) -> dict:
        """打包工程记录和成果原件，返回本地 ZIP 路径与 SHA256；保留实际审核状态。"""
        return await bridge.request("POST", project_path(project_id) + "/bundle", {})

    @server.resource("chemellm://projects/{project_id}", mime_type="application/json")
    async def project_resource(project_id: str) -> str:
        """工程项目完整状态。"""
        return json.dumps(await chem_project(project_id), ensure_ascii=False)

    @server.resource("chemellm://calculations/{project_id}/{calculation_id}", mime_type="application/json")
    async def calculation_resource(project_id: str, calculation_id: str) -> str:
        """计算完整记录。"""
        return json.dumps(await chem_calculations(project_id, calculation_id), ensure_ascii=False)

    return server
