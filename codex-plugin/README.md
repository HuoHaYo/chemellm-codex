# ChemELLM Codex 插件

本目录包含 `.codex-plugin/plugin.json`、一个公共工程技能与 DWSIM、三维、资料、费用四个专业技能，以及安装前由 `scripts/configure-codex.py` 生成的 `.mcp.json`。专项可直接使用，参考主题按需读取。仓库根目录 `.agents/plugins/marketplace.json` 将插件登记为 `chemellm@chemellm-local`。

普通 Windows 用户优先下载仓库 Releases 的安装 ZIP，完整解压后双击 `INSTALL.cmd`。安装器自动执行 Python 环境准备、配置生成、MCP 检查和以下登记步骤，无需自行安装 Git 或 Python。详细说明见仓库根目录 `安装使用说明.md`。

先按仓库 README 安装 Python 包并生成配置，再执行：

```powershell
codex plugin marketplace add "C:/path/to/ChemELLM-Codex"
codex plugin add chemellm@chemellm-local
```

配置生成器写入当前 Python 解释器及本仓库绝对路径，不写令牌，不修改用户 Codex 配置。`mcp.example.json` 仅展示格式。生成的 `.mcp.json` 不提交 Git；其他机器需自行生成。插件缓存依赖本机已安装的独立 ChemELLM Codex Python 包；不是包含 Python/FreeCAD/DWSIM 的便携运行时。

可用 `--data-root` 指定工程存储、`--allowed-root` 指定任务文件目录；重新生成时未指定的路径保持原配置。配置固定公共技能与整套技能指纹，`chem_health` 分别报告 `skillPairing` 与 `skillSetPairing`。安装前 `check-installation.py --source-only`，安装后默认检查实际缓存，或用 `--plugin-root` 指定缓存目录。原有只读资料和工程快照不随技能刷新迁移。

MCP 用 `python -X utf8 -m chemellm_codex --workspace <本仓库>` 启动，在同一进程调用本仓库内核路由。它覆盖继承的 DSH 数据路径并启用严格阶段约束，不需要访问另一个仓库或监听 TCP。独立 HTTP 模式默认端口为 8766。

协议回归命令：`.venv/Scripts/python.exe -X utf8 scripts/test_codex_mcp.py`。底层参数由 `chem_tools` 查询；自动校核不能替代专业审核。格式验证的来源、实际检查和限制见 [接入记录](../docs/codex-integration.md)。
