# DWSIM 接入

本仓库通过独立 .NET 运行器调用本机 **DWSIM Automation3**，支持稳态流程的查询、创建、载入、参数更新、求解和原生 `.dwxmz` 保存。无需另配模型 API，AI 通过 MCP 调用 DWSIM。DWSIM 为外部软件，不作为插件或 Python 包的一部分分发。

## 本机准备

安装 Windows 版 DWSIM。默认检测 `%LOCALAPPDATA%\DWSIM`、`C:\Program Files\DWSIM` 和 `C:\Program Files (x86)\DWSIM`，其他位置设置 `DWSIM_HOME` 指向含 `DWSIM.Automation.dll` 的目录。运行器使用 Windows .NET Framework 4.x；仓库包含桥接源码与编译好的运行器。

更新运行器源码或缺少 EXE 时，在本仓库执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-dwsim-runner.ps1
python -X utf8 scripts/smoke_dwsim.py
```

编译脚本用 Windows 自带 `csc.exe` 构建64位运行器，并从本机 DWSIM 安装复制程序集绑定配置。检测或编译不代表已安装 DWSIM；`smoke_dwsim.py` 会实际求解本机 Cavett 示例，未安装时明确跳过。

## AI 调用

- `chem_dwsim_status()` / `GET /v1/dwsim`：检测安装、运行器、示例和支持范围。
- `chem_dwsim(project_id, request)`：类型化的 MCP 仿真入口。
- `chem_execute(project_id, tool="dwsim", args=request)`：相同功能的通用入口，参数可用 `chem_tools(tool="dwsim")` 查询。
- HTTP：`POST /v1/projects/{project_id}/execute`，body 为 `{"tool":"dwsim","args":request}`。

请求动作、单位、参数检查和可运行建模示例见[插件中的 DWSIM 调用说明](../codex-plugin/skills/chemellm-engineering/references/dwsim.md)。

调用顺序是：检测环境 → 查询真实组分与物性包 → 保存项目要求、成果计划和设计依据 → 创建或载入流程 → 检查/更新参数 → 求解 → 使用通过校验的计算值。`catalog/inspect` 不要求先建立完整设计依据，也不新增计算记录。`create/run` 遵循 `basis` 阶段要求，并写入正式计算记录。

## 可追溯结果

每次求解使用独立子进程和本仓库数据目录中的临时工作区，stdout/stderr 写入日志，不污染 MCP 协议。载入的流程文件先复制到项目，保存哈希，原文件不覆盖。接受本仓库、当前数据目录和 DWSIM `samples` 中的 `.dwxmz/.dwxml`，沿用25 MiB单文件限制及压缩包展开限制。

项目保存原始请求、输入原件、JSON 结果、CSV 物流表和求解后原生 `.dwxmz`；记录 DWSIM 版本、运行器哈希、执行时间、温压流量单位及输入原件哈希。成果可通过 `chem_artifact` 打开，通过 `chem_calculations` 读取，通过 `chem_bundle` 随项目打包和校验 SHA256。

`inspect` 仅读取模型保存状态，不重新求解。求解未收敛时保留失败模型及错误，并返回 `validation.passed=false`；自动校核会保留失败状态。超时终止子进程，输入和日志继续保留，先查看诊断再重试。

当前连接执行**稳态求解**。结构化创建支持常用物流、混合/分流、换热、增压、阀、容器、塔及循环单元；具体模型配置是否充分由 DWSIM 求解验证。反应集、动态工况和自定义单元需在原生模型中配置，未在本接口中自动创建，也未宣称通过动态仿真验证。物性包、交互参数和模型适用性仍须结合真实资料核对；求解收敛不能代替专业审核。

## 验证

```powershell
python -X utf8 scripts/test_dwsim.py
python -X utf8 scripts/test_kernel.py
.venv\Scripts\python.exe -X utf8 scripts/test_codex_mcp.py
git diff --check
```

`test_dwsim.py` 使用独立临时项目，包含真实混合器、加热器、PR 多组分流程、输入不变、新模型保存、参数检查、未收敛和成果哈希检查；无 DWSIM 时明确跳过真实计算。MCP 回归使用实际 stdio 协议和本机 DWSIM 验证建模、属性检查、工况更新及完整结果读取。

API 依据：[DWSIM Automation3 官方文档](https://dwsim.org/api_help/html/T_DWSIM_Automation_Automation3.htm)和[官方 C# 建模示例](https://github.com/DanWBR/dwsim/blob/windows/DWSIM.Automation.Tests.CSharp/newAPI.cs)，并以本机实际反射和运行结果核对；没有复制 DWSIM 引擎代码或数据到本仓库。
