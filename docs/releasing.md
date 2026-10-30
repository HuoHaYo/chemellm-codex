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

在解压包的独立解释器中运行 `scripts/test_kernel.py`、`scripts/test_codex_mcp.py`。检查 Codex 的插件缓存含本机启动配置，实际启动该缓存配置；再次运行安装器验证重复安装，保留既有 `data/` 与 `.chem/`。记录结果后提交源码、标记版本并推送 Git，创建 GitHub Release，上传 ZIP 和 SHA256 文件，再检查公开下载。

`v0.3.0` 于 2026-10-30 完成上述验证：测试 PATH 无 Python/Git/Node/Codex 时，自动找到桌面 Codex 并准备 CPython 3.12.15；全新独立环境安装成功；HTTP 内核 2 项、真实 stdio MCP 2 项回归和发行包完整性 1 项测试通过；重复安装保留数据/快照哨兵文件；实际缓存配置启动并发现 14 个工具；uv 依赖兼容性检查通过。用户日常 Codex 配置未被本次隔离测试修改。

## 官方格式核验

2026-10-30 已读取 [OpenAI 官方打包文档](https://developers.openai.com/plugins/build/plugins) 和 [插件使用文档](https://developers.openai.com/codex/plugins)。文档明确继续支持 `.codex-plugin/plugin.json` 兼容布局、`skills` / `mcpServers` 引用、本地 marketplace，以及安装后新建会话。本版保留现有兼容布局，并补齐 marketplace 的认证策略和分类字段。发布到 GitHub Releases 是本项目的分发方式，不表示已上架 OpenAI 公共插件目录。
