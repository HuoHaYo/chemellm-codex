# QElectroTech 仪表与电气接线

需要仪表回路、控制柜接线或端子图时，定向 `chem_tools(tool="qelectrotech")`，通过现有 `chem_execute` 使用，不另开服务或重复初始化项目。仅 PLC 程序或动态控制仿真不属于此工具。

先 `chem_dependencies(dependency="qelectrotech")`。可用版本为 0.100，环境变量 `QELECTROTECH_HOME` 可指定安装目录或程序；安装遵循[共享依赖流程](dependencies.md)，已有用户安装授权可沿用。Windows 固定官方包以 SHA256 核验，使用已有 7-Zip 部署到用户 Programs，不修改文件关联；缺少前置工具不自动安装。

- `action="status"` 检测程序；`check` 只校验连接，不生成工程或计算记录。
- `create` 提供 `title/source/folios`。共同 `source` 说明连接依据，元件和导线支持例外覆盖；`docs/examples/qelectrotech-loop.json` 为明确假设的 4-20 mA 示例，不是实际设备规格。
- 每页明确 `components` 与 `wires`。端子编号、方向、PLC 通道及供电来自实际手册或明确假设；不要按仪表位号推断接线。不同电位网使用不同 `potential`，未知留空；相同网名不自动建立连接，端子桥接也必须显式连线。
- `inspect` 读取允许目录内不超过 16 MiB 的 XML `.qet`，只检查结构与端子引用。其他 QET 版本或压缩工程不保证兼容。图页间没有隐式连线。

生成自包含 `.qet`、PDF/SVG 和连接 CSV；原件内嵌自有矩形端子符号，不依赖本机符号路径，不声称 ISA/IEC 标准符号认证。当前不提供继电器接点动作或电路仿真；`kind` 是设备分类。

QET 0.100 无批量导出接口：PDF/SVG 由 ChemELLM 从同一连接数据绘制，不能声称 QET 渲染、GUI 重载或电气计算通过。内核保留真实输入、来源和检查报告；过程 JSON 默认不交付，原生 QET 按交付要求或显式选择加入。正式交付仍须确认电气选型、供电、防爆/本安及功能安全边界，自动连接检查不代替专业审核。沿用[共同交付流程](efficient-workflow.md)，不自动 ZIP。
