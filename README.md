# ChemELLM · Codex 插件开发版

这是 Codex 版的独立开发仓库。现有 DSH 版继续在桌面 `ChemELlm` 目录开发；两个目录各有独立 `.git`、源码和运行数据。

本仓库已复制最新化工内核、计算与证据库，作为 Codex 集成的开发起点。Codex 插件适配层尚未实现，当前目录不能直接作为已完成的插件安装。

## 目录

```text
app/                 计算、证据、文档与工程数据模块（来自 DSH 仓库 old/app）
kernel/              本地 HTTP 内核与图纸、安全分析、DWSIM 桥接
codex-plugin/        后续 Codex 插件适配层的开发说明
scripts/             本仓库启动脚本、内核回归与成果包校验
docs/                开发边界、源码来源与 SHA256 基线
pyproject.toml       本仓库独立的 Python 项目和依赖定义
data/、.chem/        本仓库运行数据与快照，均忽略于 Git
```

没有复制 DSH 插件、旧 Web 页面、运行数据、标准原文、虚拟环境或便携运行时。

## 开发准备

```powershell
cd "$env:USERPROFILE\Desktop\ChemELLM-Codex"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\start-kernel.cmd
```

本仓库启动脚本默认绑定 `http://127.0.0.1:8766`，与 DSH 版默认端口 `8765` 分开。项目数据和快照使用本仓库目录；启动脚本会显式设置路径，避免继承另一版的环境变量。

```powershell
# 内核健康（需先启动服务；设置了令牌时需带 x-kernel-token）
Invoke-RestMethod http://127.0.0.1:8766/v1/health

# 独立临时数据目录回归，无需运行服务
.\.venv\Scripts\python.exe -X utf8 scripts\test_kernel.py
```

FreeCAD、LibreOffice、DWSIM 为可选外部软件，按需单独安装。已有依赖沿用 DSH 内核版本；依赖升级应在本仓库单独验证。

## 两个仓库的开发约定

- DSH 版：`C:\Users\Lenovo\Desktop\ChemELlm`，继续维护 DSH Host/Client。
- Codex 版：`C:\Users\Lenovo\Desktop\ChemELLM-Codex`，在此维护 Codex 适配与本地内核。
- 源码是独立副本，没有 Git worktree、子模块、符号链接或目录共享。两边的改动不会自动同步；通用内核修复可手动移植，并在目标仓库验证、提交。
- 新仓库只在本地初始化，不预设 GitHub 远程地址。源码来源与复制时的文件哈希见 `docs/source-baseline.json`。

后续集成方向见 `codex-plugin/README.md`。本项目沿用原项目的授权状态，尚未选定开源许可证。
