# ChemELLM · Codex

面向 Codex 的本地化工工程插件：技能负责工程流程，stdio MCP 连接本仓库的计算、证据、文档、工程对象、自动校核和成果打包能力。MCP 在进程内复用 HTTP 内核，不需要预先启动服务，也不占用 TCP 端口。

当前版本 `0.3.0`，提供 Windows x64 一键联网安装包。安装后需在新 Codex 会话中加载；发布测试使用隔离配置，不修改开发者日常插件设置。

## 用户安装

从 [GitHub Releases](https://github.com/HuoHaYo/chemellm-codex/releases/latest) 下载 `ChemELLM-Codex-0.3.0-windows-x64.zip`，完整解压到长期保留的可写目录，双击 **`INSTALL.cmd`**。安装器自动准备独立 Python 3.12、安装锁定版本的依赖、检查 MCP，并注册到 Codex。无需自行安装 Python 或 Git，也无需管理员权限；首次安装需要联网访问 GitHub 和 PyPI。

先安装并登录支持插件的 Codex 桌面版或 CLI。看到 `Installed successfully` 后重启 Codex、开启新会话，即可提出化工任务。双击 **`CHECK.cmd`** 可再次检查运行环境。项目数据保存到解压目录 `data/`，快照在 `.chem/`；安装完成后保留整个目录。

详细步骤、升级和卸载见 [安装使用说明](安装使用说明.md)。发行包提供 SHA256 校验文件；不包含运行数据、个人材料、令牌、预装虚拟环境或专业外部软件。

## 安装本地开发版

需要 Python 3.11+ 和支持 `codex plugin` 的 Codex CLI。在本仓库执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -X utf8 scripts\configure-codex.py

codex plugin marketplace add "$PWD"
codex plugin add chemellm@chemellm-local
```

生成的 `codex-plugin/.mcp.json` 包含本机解释器和仓库绝对路径，已被 Git 忽略。插件缓存只包含清单、技能和该启动配置，计算引擎由已安装的本仓库 Python 包提供。**不要删除或移动本仓库及其 `.venv`**；移动目录、更换解释器或重新克隆后，重新安装 Python 包、生成配置并重新安装插件。`configure-codex.py` 只生成文件，不修改用户 Codex 设置。

新会话可请求：“使用 ChemELLM 建立水物性计算项目，记录依据、生成 Markdown 报告并打包。”FreeCAD、LibreOffice、DWSIM、Cantera 为按需使用的可选软件或依赖；相应工具的可用性取决于本机环境，MCP 接入不代表已验证全部专业模型或外部软件。

## MCP 接口

| 用途 | 工具 |
| --- | --- |
| 健康、参数目录 | `chem_health`、`chem_tools` |
| 项目 | `chem_projects`、`chem_create_project`、`chem_project` |
| 要求、计划、工程依据 | `chem_requirements`、`chem_plan`、`chem_object` |
| 当前价格、更新、变更历史 | `chem_prices`、`chem_price_save`、`chem_price_history` |
| 共享资料归档、读取、整理、检索、引用 | `chem_reference_import`、`chem_reference_read`、`chem_reference_save`、`chem_reference_search`、`chem_reference_use` |
| 计算、证据、生成 | `chem_execute`、`chem_calculations` |
| 材料、成果文件 | `chem_intake`、`chem_artifact` |
| 自动校核、打包 | `chem_review`、`chem_bundle` |

`chem_tools(tool=...)` 返回底层工具参数 schema；特殊工具没有 schema 时应查对应 `kernel/` 模块约定。完整项目和计算记录还可通过 `chemellm://projects/{project_id}`、`chemellm://calculations/{project_id}/{calculation_id}` 资源读取。

成果包含 `localPath`、字节数和 SHA256，直接用绝对路径打开。返回的 `/v1/...` 路径属于内核契约；MCP 模式不提供外部 HTTP 下载端点。计算或生成中断后应先核对项目记录再重试。

设备及其他费用保存在 `data/projects/<项目ID>/prices/prices.json`，用户可直接编辑；同目录 `history.json` 保留历次增删改的前后值、时间与理由。AI 收到真实价格后按稳定标识更新，缺价时保存有依据且明确标注的假设；不会用假设覆盖已有真实价格。价格更新使旧自动校核失效，台账与历史随成果包交付。字段与用法见 [价格台账说明](docs/pricing.md)。已安装插件需要重载 MCP、新会话加载更新后的技能，才能发现新增工具。

标准、手册及参考资料可归档到 `data/references/` 跨项目复用，保留原件、来源、全文分块和有历史版本的整理稿。图片由工具直接返回给当前 Codex 会话识读，无需另配模型 API；长资料先建索引，再按任务补充精简。项目引用固定版本及独立副本，随成果包保存。用法见 [共享资料库说明](docs/references.md)。

## 数据和工程边界

- 默认使用本仓库的 `data/`、`data/kernel/` SQLite 与成果目录，以及 `.chem/` 快照。MCP 启动会覆盖继承的 DSH 数据路径、令牌和阶段配置。
- `app/`、`kernel/` 和数据独立于旁边的 DSH 版 `ChemELlm`；不使用其源码、运行时或项目数据。内核不再回退到外部 `old/app`。
- MCP 强制要求阶段顺序；依据数值保留单位、条件、来源和理由。自动校核与专业审核分离，`ready` 不代表工程标准符合性或专业审核通过。
- 单个 MCP 进程串行执行内核请求；同一项目应由一个内核进程管理，避免多个 MCP/HTTP 进程同时写入 JSON 状态。
- 运行数据、个人材料、标准原文、令牌、虚拟环境和本机配置不纳入 Git。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\test_kernel.py
.\.venv\Scripts\python.exe -X utf8 scripts\test_codex_mcp.py
git diff --check
```

两套回归都使用独立临时数据，不需要正在运行的服务。MCP 测试从无关工作目录启动真实子进程，检查带中文/空格的存储路径、继承配置隔离、阶段约束、错误返回、完整工程流程、资源读取和成果哈希。

维护发行包时，将源文件纳入 Git 后运行 `python -X utf8 scripts/build_release.py`，产物在被 Git 忽略的 `dist/`；`python -X utf8 scripts/test_release.py` 验证逐文件哈希、排除项和构建可复现性。Windows 安装器和版本锁定的维护说明见 [docs/releasing.md](docs/releasing.md)。

需要单独的 HTTP 内核时运行 `scripts/start-kernel.cmd` 或 `scripts/start-kernel.ps1`，默认 `http://127.0.0.1:8766`；设置 `CHEMELLM_KERNEL_TOKEN` 时，请求需带 `x-kernel-token`。成果包可用 `scripts/verify_bundle.py <zip路径>` 校验。

```text
app/                       计算、证据、文档和工程数据
kernel/                    HTTP 内核与图纸、安全分析、DWSIM 桥接
chemellm_codex/             MCP 适配与独立数据环境
codex-plugin/              插件清单、技能和本机 MCP 配置
.agents/plugins/          本地 marketplace 清单
scripts/                   配置生成、启动、回归与成果包验证
release/                   运行环境引导配置、依赖版本与哈希锁
docs/                      源码来源和接入格式验证记录
```

接入与格式验证见 [codex-plugin/README.md](codex-plugin/README.md) 和 [docs/codex-integration.md](docs/codex-integration.md)。源码复制基线保留在 `docs/source-baseline.json`；该文件是历史来源记录，不随后续改动更新哈希。本项目沿用原项目授权状态，尚未选定开源许可证。

## Git 版本管理

本仓库拥有独立 `.git`，初始提交 `e9e0e54` 保存迁移前内核基线，`v0.2.0` 保存初版 Codex 接入。远程仓库为 [HuoHaYo/chemellm-codex](https://github.com/HuoHaYo/chemellm-codex)，稳定源码在 `main`，发行版本使用标签记录。使用 `git log --oneline --decorate` 查看演进。
