"""MCP tools backed by the existing HTTP contract, without opening a socket."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from kernel import server as kernel


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
                      "自动校核不能替代专业审核。失败后先核对项目状态再重试。"),
        lifespan=lifespan,
        log_level="WARNING",
    )
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    def project_path(project_id: str) -> str:
        return "/v1/projects/" + segment(project_id)

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
