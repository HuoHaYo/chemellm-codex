---
name: chemellm-engineering
description: 使用 ChemELLM 本地 MCP 做化工计算、证据整理、工程对象管理、自动校核和成果打包。适用于需要可追溯工程记录的化工任务。
---

使用本插件的 `chem_*` MCP 工具。先读 `chem_health` 确认本工作区数据位置，再用 `chem_projects` / `chem_project` 选择已有项目，或建立项目；不要把相邻 DSH 仓库当作数据或运行时来源。

需要计算或交付成果时，保存用户的实际要求（`chem_requirements`）、要求到成果的映射（`chem_plan`）和设计依据（`chem_object`），再执行计算或生成文件。MCP 默认强制阶段约束。

- 用 `chem_tools(tool=...)` 查真实参数 schema，再调用 `chem_execute`。特殊工具无 schema 时查对应内核模块的参数约定；不要编造参数或结果。
- 每个依据数值保留 `key`、`value`、`unit`、`conditions`、`origin`、`rationale`。假设明确标为假设；缺失的关键依据向用户询问。
- 导入材料使用 `chem_intake`；只接受此仓库或数据目录中的文件，最多 25 MiB。保留可定位来源及哈希；题录不等于原文。
- 大结果被截断时，用 `chem_calculations` 指定计算 ID，或读 `chemellm://calculations/{project_id}/{calculation_id}`；避免从截断回复抄数。
- 执行失败或中断可能已有部分写入。先读项目、计算和文件状态，再决定是否重试。
- 生成后用 `chem_review` 查看实际缺口，再按任务需要 `chem_bundle`。自动校核与专业审核分离；`ready` 不能表述为工程标准符合性或专业审核通过。

成果回复包含 `localPath` 和 `sha256`，可用绝对路径在 Codex 展示或打开。MCP 内核不监听 TCP，回复中 `/v1/...` 是接口标识；需要独立 HTTP 下载服务时另运行本仓库 `scripts/start-kernel.cmd`，默认端口 8766。
