# ChemELLM Codex 插件

本目录包含 `.codex-plugin/plugin.json`、`chemellm-engineering` 技能，以及安装前由 `scripts/configure-codex.py` 生成的 `.mcp.json`。仓库根目录 `.agents/plugins/marketplace.json` 将插件登记为 `chemellm@chemellm-local`。

普通 Windows 用户优先下载仓库 Releases 的安装 ZIP，完整解压后双击 `INSTALL.cmd`。安装器自动执行 Python 环境准备、配置生成、MCP 检查和以下登记步骤，无需自行安装 Git 或 Python。详细说明见仓库根目录 `安装使用说明.md`。

先按仓库 README 安装 Python 包并生成配置，再执行：

```powershell
codex plugin marketplace add "C:/path/to/ChemELLM-Codex"
codex plugin add chemellm@chemellm-local
```

配置生成器写入当前 Python 解释器及本仓库绝对路径，不写令牌，不修改用户 Codex 配置。`mcp.example.json` 仅展示格式。生成的 `.mcp.json` 不提交 Git；其他机器需自行生成。插件缓存依赖本机已安装的独立 ChemELLM Codex Python 包；不是包含 Python/FreeCAD/DWSIM 的便携运行时。

MCP 用 `python -X utf8 -m chemellm_codex --workspace <本仓库>` 启动，在同一进程调用本仓库内核路由。它覆盖继承的 DSH 数据路径并启用严格阶段约束，不需要访问另一个仓库或监听 TCP。独立 HTTP 模式默认端口为 8766。

协议回归命令：`.venv/Scripts/python.exe -X utf8 scripts/test_codex_mcp.py`。底层参数由 `chem_tools` 查询；自动校核不能替代专业审核。格式验证的来源、实际检查和限制见 [接入记录](../docs/codex-integration.md)。
