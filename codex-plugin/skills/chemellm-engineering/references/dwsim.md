# DWSIM 稳态仿真调用

`chem_dwsim_status()` 不需要项目，返回本机安装、运行器及示例路径。`ready` 仅表示检测到运行环境，不能据此宣称模型求解成功。需要 Windows、.NET Framework 和独立安装的 DWSIM；`DWSIM_HOME` 可指向安装目录。

本机缺少 DWSIM 时按 [可选工具安装流程](dependencies.md) 说明当前仿真为什么需要它，展示安装方案并询问用户，收到明确同意后安装和验证，再继续仿真。仅运行器或路径有问题时先修复已有环境。

`chem_dwsim(project_id, request={...})` 通过与 `chem_execute(tool="dwsim", args={...})` 相同的内核运行。

| action | 输入 | 用途 |
| --- | --- | --- |
| `catalog` | `compound_query`、`offset`、`limit` 可选 | 查询实际组分名、CAS、分子式、物性包和可创建对象类型 |
| `inspect` | `flowsheet_path`；可选 `object_tags` | 查看位号、端口、枚举参数及属性 ID/单位；仅载入，不重新求解 |
| `create` | `model` | 创建结构化流程、求解、保存新原生模型 |
| `run` | `flowsheet_path`；可选 `updates` | 复制输入原件，更新已有对象、求解、另存新文件 |

前三种查询 `status/catalog/inspect` 可在设计依据尚未建立前调用；`create/run` 遵循项目阶段要求。`timeout_seconds` 默认120秒，范围5–900秒；超时会终止运行器，输入和日志保留在返回的目录中。不要无判断地增加时限重试，先检查设置及诊断。

`inspect` 省略 `object_tags` 时返回对象/端口概览，按 `offset/limit` 分页。展开指定对象时提供如 `object_tags=["HT-101"]`，查看 `parameters` 与 `properties`；属性以 `property_offset/property_limit` 分页。完整检查 JSON 的位置在 `inspection_path`。`parameters` 的枚举可选值在 `choices`；数值参数遵循 DWSIM 原生 API，优先使用明确返回单位的 `properties`。属性 ID 可能随模型/版本变化，先查再用。例如只有 inspect 确认 `PROP_HT_2` 是可写、单位为 K 的出口温度后，才能设置该属性。

模型必须明确填写 `compounds`、`property_package`、`objects` 和连接。对象 `type` 使用 catalog 的准确名称；常用对象有 MaterialStream、EnergyStream、Mixer、Splitter、Heater、Cooler、Valve、Pump、Vessel、HeatExchanger 及塔模型。声明对象不意味着其全部模型参数已具备；复杂塔模型需先创建/检查、补齐设置，或使用已配置原生文件。自定义单元、反应集和动态模型不通过本接口自动构造。

每个进料显式填写 `temperature_k`、`pressure_pa`、`mass_flow_kg_s`、`mole_fractions`；压力为绝压 Pa，组成逐一列出全部组分且和为1，不自动归一化。其余对象可用 `properties={属性ID: 数值}` 以及 `parameters={模型标量名: 值}`；不接受任意代码执行。连接包含 `source`、`target`、`source_port` 和 `target_port`，端口从0开始。

下面是可直接作为 `request` 的混合器合成测试例。工况用于接口验证，不是项目设计依据；实际任务应替换输入并说明来源。

```json
{
  "action": "create",
  "model": {
    "compounds": ["Water"],
    "property_package": "Steam Tables (IAPWS-IF97)",
    "objects": [
      {"tag": "COLD", "type": "MaterialStream", "x": 50, "y": 50,
       "temperature_k": 300, "pressure_pa": 200000, "mass_flow_kg_s": 2, "mole_fractions": {"Water": 1}},
      {"tag": "HOT", "type": "MaterialStream", "x": 50, "y": 160,
       "temperature_k": 350, "pressure_pa": 200000, "mass_flow_kg_s": 1, "mole_fractions": {"Water": 1}},
      {"tag": "MIX-101", "type": "Mixer", "x": 220, "y": 100},
      {"tag": "PRODUCT", "type": "MaterialStream", "x": 380, "y": 100}
    ],
    "connections": [
      {"source": "COLD", "target": "MIX-101", "target_port": 0},
      {"source": "HOT", "target": "MIX-101", "target_port": 1},
      {"source": "MIX-101", "target": "PRODUCT"}
    ]
  }
}
```

从响应 `artifacts` 中找到 `DWSIM流程.dwxmz` 的 `localPath` 后，可以：

```json
{"action": "run", "flowsheet_path": "<返回的绝对路径>",
 "updates": [{"tag": "HOT", "temperature_k": 370}]}
```

`updates.tag` 使用位号或内部名称，不重命名对象；`type/x/y` 在更新时不改变结构。调整拓扑请修改创建请求或使用原生 DWSIM 文件，不把多份工况结果串成一次模型计算。

结果 `streams` 包含 K、Pa、kg/s、组分及摩尔分数；`energy_streams` 为 kW，单元属性分别附单位。响应 `calculationId` 可交给 `chem_calculations` 读取完整输入/结果，避免从截断值抄数。`validation.passed` 检查求解状态及基本结果读取，不证明物性和二元参数适用、模型精度或工程标准符合性。

成果包括 `DWSIM输入.json`、`DWSIM结果.json`、`DWSIM物流表.csv`、`DWSIM流程.dwxmz`；载入文件时还保存原件与 SHA256。求解未收敛仍保存模型、错误及失败计算记录，不能写成成功。成功结果返回 DWSIM 版本、运行器哈希、时间和输入来源；文件随项目成果包打包。
