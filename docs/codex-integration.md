# Codex 接入验证记录

验证日期：2026-10-29；本机 Codex CLI：`0.164.0`；MCP Python SDK：`1.30.0`。此记录描述本次可复现的验证，不保证其他 Codex 版本、组织策略或账户权限完全相同。

## 格式来源

首先尝试检索和读取官方页面 `https://developers.openai.com/codex/plugins/`、`https://developers.openai.com/codex/mcp/` 及配置参考页。本站在当前环境返回 HTTP 403（Vercel deny），未读取到页面正文，因此本次不声称已完成在线文档核验。

使用本机官方 Codex 分发物补充核验：

- 官方随附 `codex-app-tools` 插件的 `.codex-plugin/plugin.json` 与 `.mcp.json`，确认 `mcpServers` 文件引用和 stdio `command` / `args` 结构。
- 官方随附 `visualize` / `pdf` 插件清单，确认 `name`、`version`、`description`、`skills` 的形式。
- `codex plugin --help`、`codex plugin marketplace add --help`、`codex plugin add --help`、`codex mcp add --help`，确认本机 CLI 的安装命令和 stdio 启动参数。
- 本机官方 Codex CLI 的 marketplace 解析器，实际读取 `.agents/plugins/marketplace.json` 并识别本插件。

未采用 DSH 的 `cordis.patch.yml` 或 Host/Client 协议。

## 可复现的插件发现检查

生成 `.mcp.json` 后，在本仓库执行下列只读检查。`-c` 为本次命令覆盖，不向用户全局配置登记 marketplace：

```powershell
codex -c 'marketplaces.chemellm-local.source_type="local"' `
  -c 'marketplaces.chemellm-local.source="C:/path/to/ChemELLM-Codex"' `
  plugin list --marketplace chemellm-local --available --json
```

本次返回 `pluginId: chemellm@chemellm-local`、`version: 0.2.0`、`installPolicy: AVAILABLE`；`installed` 与 `enabled` 都是 `false`。这是发现和清单解析验证，不是用户会话已安装、已授权或已经调用工具的证明。安装步骤见仓库 README；需要在实际使用的 Codex 中安装并开启新会话。

另在临时 Codex 配置目录下执行了真实 `plugin marketplace add`、`plugin add chemellm@chemellm-local` 和 `plugin list --json`，均成功。临时环境显示 `installed: true`、`enabled: true`，并检查缓存确实包含生成的 `.mcp.json`、且其中解释器可解析；证明本机配置虽被 Git 忽略，仍能随本地插件安装进入缓存。临时配置只传给测试子进程，验证后删除，未修改用户日常 Codex 配置。

## 执行与数据隔离

`chemellm_codex.__main__` 在导入内核前，固定当前工作区数据路径、快照、内部令牌和严格阶段约束。启动配置给出明确的仓库路径，避免插件缓存目录或 Codex 当前工作目录改变项目数据位置。

MCP 通过 `httpx.ASGITransport` 在进程内调用原 HTTP 路由；不产生网络监听，不连接可能属于 DSH 的现有服务。令牌只存在于该子进程环境与内存，避免把内部令牌写入配置或交付成果。科学库输出和日志发往 stderr，stdout 专供 MCP。

`chem_execute` 保留底层工具结果、校核字段和错误；通过 `chem_tools` 暴露可执行目录及已定义的参数 schema。文件结果补充真实本地路径和 SHA256。请求在单进程内串行；底层 JSON 项目状态不支持多个服务进程同时写入同一项目。

`scripts/test_codex_mcp.py` 使用官方 SDK 的 stdio 客户端启动子进程，从无关目录连接并执行完整流程；用临时数据验证不触及继承的 DSH 路径，验证中文/空格路径、失败调用、导入边界、阶段阻止、自动审核状态、完整结果资源和 ZIP 内文件哈希。`scripts/test_kernel.py` 保留独立的 HTTP 内核回归。

本次结果：HTTP 内核 2 项测试通过，stdio MCP 2 项测试通过，技能 `quick_validate.py` 通过，`pip check` 通过。MCP 回归还验证成果原件或 ZIP 缺失时仍可读取项目，并返回 `localFileMissing`，便于核对恢复。

现有化工模型、图纸和可选外部软件的专业正确性不由本次适配回归证明。原始源码基线保留于 `source-baseline.json`；适配代码的后续演进由 Git 提交和版本标签记录。
