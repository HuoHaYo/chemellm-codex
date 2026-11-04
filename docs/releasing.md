# 发行包维护

Windows x64 发行包是可自引导的联网安装 ZIP，包含已纳入 Git 的源码、技能、清单、安装入口和依赖锁，不包含虚拟环境、便携运行时或用户数据。`INSTALL.cmd` 使用 Windows 自带 PowerShell 5.1 调用 `scripts/install-windows.ps1`。

## 运行环境

`release/bootstrap.json` 固定 uv 版本、下载 URL、官方 Release 提供的 SHA256 和 CPython 版本。安装器校验 uv 后，使用 uv 准备包内 `runtime/python/` 和 `.venv/`；不修改全局 Python、PATH 或相邻 DSH 仓库。

`release/requirements-windows-x64.lock` 固定全部 71 项运行/构建依赖及发行文件哈希，针对 CPython 3.12 / Windows x64 解析；只安装二进制 wheel，不要求用户配置 C/C++ 编译器。源码以 editable 形式安装，保证既有内核相对文件路径与材料导入范围仍以本包为根目录。

更新依赖时用受信任的 uv 运行，随后必须完成干净环境验证：

```powershell
uv pip compile pyproject.toml release/bootstrap.in --python-version 3.12.15 `
  --python-platform x86_64-pc-windows-msvc --generate-hashes --only-binary :all: `
  --no-header --no-annotate --output-file release/requirements-windows-x64.lock --quiet
```

## 构建

同步 `pyproject.toml`、`chemellm_codex/__init__.py`、插件清单的版本，将发行源文件纳入 Git，然后执行：

```powershell
python -X utf8 scripts/build_release.py
python -X utf8 scripts/test_release.py
```

构建器从 Git 索引取文件清单，按目录白名单打包；排除本机 `.mcp.json`、运行环境及数据，生成逐文件 SHA256 清单，并把 Windows 脚本编码和换行标准化。相同输入生成相同 ZIP 和 SHA256；输出为 `dist/ChemELLM-Codex-<版本>-windows-x64.zip`、`dist/SHA256SUMS.txt`。构建后的 ZIP 不纳入 Git。

## 发行验证

在包含中文和空格的全新目录中完整解压；为子进程指定临时 Codex 配置目录，运行安装器。在测试 PATH 中移除 Python、Git、Node 和 Codex，验证安装器能找到已安装的桌面 Codex，并自行下载独立 Python。

在解压包的独立解释器中运行 `scripts/test_kernel.py`、`scripts/test_codex_mcp.py`、`scripts/test_dependencies.py` 和 `scripts/test_dwsim.py`；未安装 DWSIM 时如实记录跳过的真实仿真测试。检查 Codex 的插件缓存含本机启动配置，实际启动该缓存配置；再次运行安装器验证重复安装，保留既有 `data/` 与 `.chem/`。记录结果后提交源码、标记版本并推送 Git，创建 GitHub Release，上传 ZIP 和 SHA256 文件，再检查公开下载。

`v0.3.0` 于 2026-10-30 完成上述验证：测试 PATH 无 Python/Git/Node/Codex 时，自动找到桌面 Codex 并准备 CPython 3.12.15；全新独立环境安装成功；HTTP 内核 2 项、真实 stdio MCP 2 项回归和发行包完整性 1 项测试通过；重复安装保留数据/快照哨兵文件；实际缓存配置启动并发现 14 个工具；uv 依赖兼容性检查通过。用户日常 Codex 配置未被本次隔离测试修改。

`v0.4.0` 于 2026-10-31 在中文及空格路径中重新完成全新安装，测试 PATH 仅保留 Windows 系统目录；独立 CPython 3.12.15 虚拟环境不继承系统包，71 项锁定依赖与本项目安装成功。该环境内核 20 项、真实 stdio MCP 6 项、依赖安装流程 7 项和 DWSIM 6 项回归全部通过；DWSIM 使用本机实际引擎，没有跳过真实计算。加上发行包完整性与可重复构建 1 项，共 40 项测试通过。独立 Codex 配置注册 `0.4.0`，实际插件缓存启动发现 28 个工具；重复安装保留 `data/` 和 `.chem/` 哨兵文件，uv 依赖兼容性检查通过。依赖安装测试使用模拟安装器或拒绝未授权调用，没有实际安装可选工程软件。

## v0.5.0 验证

2026-11-04 在中文和空格路径中建立独立 CPython 3.12.15 虚拟环境，`include-system-site-packages=false`，安装71项锁定依赖及本项目；测试PATH只保留Windows系统目录。GitHub引导下载在本次网络环境中未能完成，改用当前仓库v0.4.0安装测试保留的官方uv压缩包（重新校验SHA256）、Python与依赖下载缓存，以离线方式新建环境；没有复用原虚拟环境。本次不宣称完成无缓存联网下载验收。

发行包复测发现解压时间会使运行器被误判为需要重建，改为SHA256内容指纹，并用回归测试覆盖解压时间、CRLF换行、源码变化、二进制变化及清单缺失/损坏。源码、EXE及指纹随包发布。

独立环境执行内核20项、stdio MCP 6项、DWSIM稳态扩展20项、工况状态2项和依赖流程7项，均通过；真实DWSIM 9.0.5案例没有跳过。开发环境额外执行DWSIM基础及指纹8项、可重复构建及包清单1项，均通过。依赖兼容性检查确认72个已安装包兼容；独立Codex配置成功安装0.5.0，实际缓存启动发现30个工具，存储隔离与阶段约束检查通过。

独立环境另执行DWSIM基础及指纹8项通过；重复运行完整安装器成功刷新隔离插件缓存，`data/`与`.chem/`哨兵内容保留。没有修改开发者日常Codex配置。

本次暂存源码和安装ZIP经Gitleaks及补充路径/密钥格式检查；命中仅为已复核的测试设备条目key。安装ZIP移除本机来源路径，排除本机MCP配置、SSH配置、运行数据、网站和部署目录。历史提交及旧发行包未重写。网站版本和安装包名由发布版本统一检查，并使用同一SHA256清单核对下载。

## 官方格式核验

2026-10-30 已读取 [OpenAI 官方打包文档](https://developers.openai.com/plugins/build/plugins) 和 [插件使用文档](https://developers.openai.com/codex/plugins)。文档明确继续支持 `.codex-plugin/plugin.json` 兼容布局、`skills` / `mcpServers` 引用、本地 marketplace，以及安装后新建会话。本版保留现有兼容布局，并补齐 marketplace 的认证策略和分类字段。发布到 GitHub Releases 是本项目的分发方式，不表示已上架 OpenAI 公共插件目录。

2026-10-31 发布 `v0.4.0` 前再次读取官方打包文档和[技能文档](https://developers.openai.com/codex/skills)，确认兼容清单及带 `name` / `description` 的 `SKILL.md` 仍受支持。本版沿用现有结构；Windows PowerShell 5.1 的 uv 引导下载补充显式使用 `HTTPS_PROXY`，与后续 uv 下载的代理配置保持一致。

2026-11-04 尝试再次获取上述官方页面（包括浏览器），均返回HTTP 403，未声称完成新的在线格式确认。v0.5.0保留现有清单布局，仅同步版本；技能格式校验通过，并用本机Codex CLI在独立配置目录实际完成插件安装和缓存启动验证。
