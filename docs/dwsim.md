# DWSIM 接入

本仓库通过独立 .NET 运行器调用本机 **DWSIM Automation3**，支持稳态流程的查询、创建、载入、参数更新、求解和原生 `.dwxmz` 保存。无需另配模型 API，AI 通过 MCP 调用 DWSIM。DWSIM 为外部软件，不作为插件或 Python 包的一部分分发。

## 本机准备

安装 Windows 版 DWSIM。默认检测 `%LOCALAPPDATA%\DWSIM`、`C:\Program Files\DWSIM` 和 `C:\Program Files (x86)\DWSIM`，其他位置设置 `DWSIM_HOME` 指向含 `DWSIM.Automation.dll` 的目录。运行器使用 Windows .NET Framework 4.x；仓库包含桥接源码与编译好的运行器。

更新运行器源码或缺少 EXE 时，在本仓库执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-dwsim-runner.ps1
python -X utf8 scripts/smoke_dwsim.py
```

编译脚本用 Windows 自带 `csc.exe` 构建64位运行器，并从本机 DWSIM 安装复制程序集绑定配置，同时生成 `DwsimRunner.build.json`，记录 EXE 及规范化源码的 SHA256。运行器新旧由内容指纹判断，解压时间或 Git 的换行转换不会导致误判；修改源码或二进制后需重新构建。检测或编译不代表已安装 DWSIM；`smoke_dwsim.py` 会实际求解本机 Cavett 示例，未安装时明确跳过。

## AI 调用

- `chem_dwsim_status()` / `GET /v1/dwsim`：检测安装、运行器、示例和支持范围。
- `chem_dwsim(project_id, request)`：类型化的 MCP 仿真入口。
- `chem_execute(project_id, tool="dwsim", args=request)`：相同功能的通用入口，参数可用 `chem_tools(tool="dwsim")` 查询。
- `chem_dwsim_study(project_id, request)`：串行多工况/扫描、状态、取消和续算；HTTP 为 `POST /v1/projects/{project_id}/dwsim/study`。
- `chem_dwsim_publish(project_id, request)`：显式映射通过校核的结果到有版本检查的工程对象，并可比较参考数据；HTTP 为 `POST /v1/projects/{project_id}/dwsim/publish`。
- HTTP：`POST /v1/projects/{project_id}/execute`，body 为 `{"tool":"dwsim","args":request}`。

请求动作、单位、参数检查和可运行建模示例见[插件中的 DWSIM 调用说明](../codex-plugin/skills/chemellm-engineering/references/dwsim.md)。

原生布局编辑使用 `chem_dwsim(action="layout")`，支持自动拓扑分层、工艺分区、固定设备和局部坐标/尺寸/方向调整。`inspect(view="layout")` 返回原生图形、端口坐标、连接路径和 PNG。布局结果保留原件，另存 DWXML/DWXMZ，并在保存后实际重开；仅合并允许的图形字段与专用布局元数据，工艺和连接数据保持一致。该操作登记成果及布局历史，不新增计算记录或数值验收。`create/run` 可选提供同一 `layout`；省略时保持原有行为。

布局回归：`.venv/Scripts/python.exe -X utf8 scripts/test_dwsim_layout.py`，覆盖循环/分支/孤立节点、固定冲突、中文分区、两种原生格式、连续编辑、未知扩展及压缩包附属文件保护、保存后复算。预览使用本机 DWSIM 自带 SkiaSharp，编译运行器需从已安装的 DWSIM 引用该 DLL，插件不分发它。

八步功能及本机回归结果见[稳态扩展验收记录](dwsim-acceptance.md)。

调用顺序是：检测环境 → 查询物性与依据 → `check` 建模前检查 → 配置/求解 → 独立守恒校核 → 独立参考数据比较 → 多工况 → 发布设计量。`catalog/inspect/check` 不要求先完成依据阶段，也不新增计算记录。`create/run` 遵循 `basis` 阶段要求，并写入正式计算记录；可用 `basis_keys` 固定关联已有依据。

## 可追溯结果

每次求解使用独立子进程和本仓库数据目录中的临时工作区，stdout/stderr 写入日志，不污染 MCP 协议。载入的流程文件先复制到项目，保存哈希，原文件不覆盖。接受本仓库、当前数据目录和 DWSIM `samples` 中的 `.dwxmz/.dwxml`，沿用25 MiB单文件限制及压缩包展开限制。

项目保存原始请求、输入原件、JSON 结果、CSV 物流表和求解后原生 `.dwxmz`；记录 DWSIM 版本、运行器哈希、执行时间、温压流量单位及输入原件哈希。成果可通过 `chem_artifact` 打开，通过 `chem_calculations` 读取，通过 `chem_bundle` 随项目打包和校验 SHA256。

`inspect` 仅读取模型保存状态，不重新求解。求解未收敛时保留失败模型及错误，并返回 `validation.passed=false`；自动校核会保留失败状态。超时终止子进程，输入和日志继续保留，先查看诊断再重试。

当前扩展覆盖八个环节：只检查不求解；单元和全流程质量/组分/能量独立校核；按单元物性包与二元参数来源管理；结构化热工/分离和严格塔；循环初值、显式初始化与实际诊断；最多50工况的持久串行任务；转化率/平衡反应及元素校核；有版本检查的工程对象发布、参考数据报告和数据表映射。详见插件调用说明中的字段和例子。动态、动力学CSTR/PFR和代码速率表达式不纳入。

`solved` 保留引擎状态。`validation.passed` 全部必需检查完整通过才为true，失败false，信息不足null。质量与组分绝对容差1e-8 kg/s，能量1e-4 kW，相对1e-5，元素1e-7 mol/s，可显式配置并随结果保存。旧记录不追溯修改。守恒通过不等于模型精度或物性适用性已验证；原生默认零二元参数不是实验数据。缺少实测/文献数据时保留模型验证缺口。

工况固定基准与哈希，每次独立原生模型和计算；失败点不能进入有效包络，取消在当前单次求解结束后停止，重启后显式resume继续未完成点。任务不增加后台监控。同项目写入互斥。发布只用明确选定且校核通过的结果，操作条件/模拟负荷与机械设计温压/裕量分开；数据表的绝压到表压换算需要明确环境压力。价格匹配继续使用已有台账及备注。

## 验证

```powershell
python -X utf8 scripts/test_dwsim.py
python -X utf8 scripts/test_dwsim_steady.py
python -X utf8 scripts/test_dwsim_study_state.py
python -X utf8 scripts/test_kernel.py
.venv\Scripts\python.exe -X utf8 scripts/test_codex_mcp.py
git diff --check
```

`test_dwsim.py` 使用独立临时项目；`test_dwsim_steady.py` 要求真实引擎，不将跳过当通过。独立预期包括解析守恒、CoolProp水焓、严格塔稳定基准、[NIST WebBook](https://webbook.nist.gov/chemistry/)气相WGS平衡常数参考点。真实案例覆盖水/蒸汽、PR烃类、NRTL、泵/压缩机/阀/冷却/闪蒸/HX/简捷塔/严格塔、循环失败和显式初始化、转化率和平衡、多工况部分失败、工程对象版本及数据表。状态机测试单独覆盖取消/中断恢复，不冒充真实引擎精度验收。stdio MCP 回归实际调用check、study和publish，并检查单位与版本冲突。

API 依据：[DWSIM Automation3 官方文档](https://dwsim.org/api_help/html/T_DWSIM_Automation_Automation3.htm)和[官方 C# 建模示例](https://github.com/DanWBR/dwsim/blob/windows/DWSIM.Automation.Tests.CSharp/newAPI.cs)，并以本机实际反射和运行结果核对；没有复制 DWSIM 引擎代码或数据到本仓库。

本轮在线GitHub请求连接被重置，使用本机官方XML文档、反射及真实求解交叉核对；发现CreateEquilibriumReaction的XML参数标签顺序与9.0.5实际签名有差异，桥接按实际签名实现。NRTL/UNIQUAC温度形式通过本机程序集核对。源码仅包含有限API映射，不随包分发引擎或其数据库。不同DWSIM版本须重新验收。
