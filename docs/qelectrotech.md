# QElectroTech 接入

通过原有 `chem_tools` / `chem_execute` 接入，同一个 MCP，MCP 工具数量保持 34。内核执行目录新增 `qelectrotech`。本实现覆盖明确连接数据的仪表、电气接线，不包含 PLC/DCS 程序运行或动态工艺仿真。

## 安装与环境

已验证固定版本：QElectroTech 0.100.0 / git8590 / Windows x64。

- [官方入口](https://qelectrotech.org/download.php)、[官方发布](https://github.com/qelectrotech/qelectrotech-source-mirror/releases/tag/0.100)。
- 安装包 SHA256：`14a830e391b78fd7d69d793d13f5f4d24696628b7a682077d3f7332fa41aaa80`。
- `scripts/install-qelectrotech.py` 使用现有 7-Zip 提取经大小和哈希校验的官方包到 `%LOCALAPPDATA%/Programs/QElectroTech`，保留程序、符号库及许可证；不执行管理员安装器，不改变文件关联，已有目录拒绝覆盖。可用 `--archive` 指向已下载的相同官方包，仍须校验。
- `QELECTROTECH_HOME` 可指定其他安装目录或可执行文件。`chem_dependencies(dependency="qelectrotech")` 检测环境及准备安装流程。
- Windows 静态 Qt 包使用 `QT_QPA_PLATFORM=windows` 检测版本；`offscreen` 会导致该官方程序异常退出。

## 操作

已知用途直接 `chem_tools(tool="qelectrotech")`，再用原有 `chem_execute(project_id, tool="qelectrotech", args=...)`。

`action=create/check` 使用 `title/source/folios`。每页给出 `components`（位号、明确端子、可选绘图坐标和来源例外）及 `wires`（起止端子、导线编号、可选电缆/芯线、截面积及来源例外）。`section_mm2` 单位固定为平方毫米，x/y 为绘图坐标，无工艺尺寸含义。截面积省略时明确待选型。

[两线制示例](examples/qelectrotech-loop.json) 假设无源 4-20 mA 变送器接有源隔离 AI，明确接线箱端子桥接；不是实际型号和通道配置。连接需由厂家手册、用户资料或明确假设提供，不按仪表位号推断。

`check` 校验元件/端子/导线唯一性、端子引用、自连接、重复连线、电缆芯线重复占用、绘图边界/重叠及同一导电网络的网名冲突。相同网名不自动连通，图页间无隐式连线。备用端子和待选型截面积作为提示。

`inspect` 从 MCP roots / `--allowed-root` / 项目数据目录读取不超过 16 MiB 的 XML `.qet`，禁用实体扩展，只检查结构和引用；其他版本、压缩 QET 和图页间连接不保证兼容。`status/check/inspect` 不创建计算、成果或运行记录。

## 文件与审核边界

输出自包含 `接线工程.qet`、`接线图.pdf`、逐页 SVG、`连接表.csv`。原生工程内嵌自有矩形端子符号，免除本机符号路径依赖；`kind` 为设备分类，当前不表达继电器接点动作，不声称标准符号认证。JSON 输入和检查报告作为内部过程文件保存。

QET 0.100 没有无界面批量导出命令。PDF/SVG 从共同连接模型由 ChemELLM/ReportLab 绘制，不是 QET 渲染。`native_schema_verified` 只表示序列化后结构、数量及引用匹配；`native_reload_verified=false` 表示没有完成 GUI 重载验证。

文件沿用现有哈希与计算关联。QET 原生模型仅按交付要求或明确选择入包；PDF/SVG/CSV 可直接交付。输入快照和检查 JSON 默认不入包。不自动 ZIP，不更新历史项目或运行中的插件缓存。

连接自动校核不代替电气选型、供电、电流负载、防爆/本安、SIS/功能安全或施工图专业审核。

## 验证

独立临时目录运行 `scripts/test_qelectrotech.py`、`scripts/test_kernel.py`、`scripts/test_codex_mcp.py` 及交付/依赖/技能配对回归。真实 stdio 用原工具执行新能力，核对记录关联与成果角色；图纸另经渲染检查。不会写入 cpc 既有工程。

本机验收通过：内核 22、真实 stdio 12（含实际 Blender/DWSIM 调用）、接线 6、依赖 8、交付 8、技能效率 6、依据 10、效率 9、发行构建 1，共 82 项。官方技能格式与实际插件缓存的整套指纹检查通过。发行构建仅在临时目录验证，不留下发行 ZIP。

本机安装在 `%LOCALAPPDATA%/Programs/QElectroTech`；通过官方 `codex plugin add chemellm@chemellm-local` 统一刷新缓存，实际缓存及启动配置检查通过。数据目录继续指向 `cpc/chemellm-data`，允许目录继续为 `cpc`。当前进程不重新加载；新会话使用更新。原生 GUI 重载尚未验证。
