# ChemELLM Codex 开发约定

- 当前仓库是 Codex 版，DSH 版位于旁边的 `ChemELlm` 仓库。默认只编辑当前仓库；不要把另一仓库当作运行时依赖。
- `app/` 是实际计算和证据库，`kernel/` 是本地 HTTP 服务，`chemellm_codex/` 是 stdio MCP 适配层，`codex-plugin/` 是技能与插件清单。插件安装前需安装本仓库 Python 包并生成本机 MCP 配置；不要宣称尚未执行的安装已完成。
- 本仓库默认端口是 8766。本机的实际工程测试数据、SQLite、成果、资料和工程快照存放在相邻的 `cpc/chemellm-data/`；本机 MCP 启动配置通过 `--data-root` 指向该目录，并用 `--allowed-root` 允许 `cpc/`。开发仓库旧 `data/` 下的工程目录及 `.chem/` 仅保留兼容目录链接，供历史记录和脚本使用，不要沿链接重复复制工程数据。
- 插件源码、开发环境、发行构建、`data/qa/` 与开发验收输出保留在本仓库；回归测试继续使用独立临时数据目录，不向 `cpc` 的既有工程写测试数据。不要读取或修改 DSH 版的项目数据。
- 不提交虚拟环境、运行数据、便携运行时、令牌、标准原文或个人材料。
- 改动内核后运行 `python -X utf8 scripts/test_kernel.py`。该脚本使用独立临时数据目录。
- 改动 MCP 后用已安装本项目的解释器运行 `python -X utf8 scripts/test_codex_mcp.py`；测试使用真实 stdio 协议和独立临时数据目录。MCP stdout 只用于协议，日志输出到 stderr。
- 创建 Codex 插件清单、MCP 配置或技能时，先核对当前官方格式；不要直接复用 DSH 的 Cordis 配置。
- 保持数值的单位、条件和来源；自动校核与专业审核分离，不编造计算或工程标准符合性结论。
