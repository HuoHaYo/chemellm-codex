# ChemELLM Codex v0.3.0

首个面向 Windows x64 用户的一键联网安装包。

## 安装

1. 下载本页附件 **ChemELLM-Codex-0.3.0-windows-x64.zip** 并完整解压到可长期保留的可写目录。
2. 双击 **INSTALL.cmd**，自动准备独立 Python 3.12、安装经过哈希校验的锁定依赖、检查 MCP，并登记到 Codex。
3. 看到 `Installed successfully` 后重启 Codex、新建会话。试用：“使用 ChemELLM 建立水物性计算项目，记录依据，生成 Markdown 报告并打包。”

先安装并登录支持插件的 Codex 桌面版或 CLI。无需自行安装 Python、Git，也无需管理员权限。首次安装需联网访问 GitHub 和 PyPI；双击 **CHECK.cmd** 可检查环境。

## 本版变化

- 自动检测常见 Codex 桌面目录和 CLI，支持中文及空格路径。
- 独立运行环境位于包内 `runtime/` 与 `.venv/`，不更改系统 Python。
- 固定 CPython 3.12.15、uv 0.13.6 和 71 项运行/构建依赖，并校验下载哈希。
- 安装前检查实际 MCP 启动和工具发现；重复安装刷新本机配置并保留原目录的项目数据。
- 提供中文安装/升级/卸载说明、逐文件清单和 SHA256 校验附件。

项目和成果保存在安装目录 `data/`，工程快照在 `.chem/`。安装后不要移动整个目录；升级前关闭相关 Codex 会话并备份数据。LibreOffice、FreeCAD、DWSIM、Cantera 等专业外部能力按需另行配置。

已验证全新环境安装、中文/空格路径、重复安装保留数据，以及 Codex 缓存启动配置；4 项内核/MCP 回归和发行包完整性测试通过。

数值保留单位、条件和来源；自动校核不代表专业审核通过。此发行包通过 GitHub 分发，未声明已上架 OpenAI 公共插件目录。
