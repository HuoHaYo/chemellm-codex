# DWSIM 基础建模


| action | 输入 | 行为 |
| --- | --- | --- |
| catalog | 可选 compound_query、offset/limit | 实际组分名、CAS、物性包和对象类型 |
| inspect | flowsheet_path；可选 object_tags | 只载入缓存，读端口、标量参数、属性ID与单位 |
| check | model 或 flowsheet_path；文件可带 updates | 只检查，不求解、不创建正式计算、不改原件 |
| create | model | 创建、求解、保存独立原生模型和计算记录 |
| run | flowsheet_path、updates、initialization、property_packages | 复制原件、修改、求解、另存新文件 |
| layout | flowsheet_path；可选 layout、output_format | 编辑原生图形，另存模型、预览和布局报告；不求解、不创建计算记录 |

status/catalog/inspect/check 不要求先完成依据阶段。新流程 create/run 只要求项目存在及参数有效；旧项目保留阶段要求。正式 publish 要求必要依据已关联且校核通过。`basis_keys=["basis/feed"]` 关联已有工程依据。缺失来源或尚未支持的自由度判断显示待核对；明确的连接、进料、规格缺项定位到位号。check 的 `preflight.ready_to_solve` 不代表模型已经求解或通过全部自由度分析。

`model` 必填 compounds/property_package/objects/connections。进料明确 temperature_k、pressure_pa、mass_flow_kg_s 和全部组分 mole_fractions，和为1，不自动归一化。温度K，压力绝压Pa，质量流量kg/s，摩尔流量mol/s，热流/轴功kW，质量焓kJ/kg。连接 source/target 的端口从0开始，先读取真实端口名和类型，不猜能量口。

原有 parameters/properties 保持兼容。结构化 spec 与同参数重复或额外目标冲突会拒绝。原生属性按 inspect 的 writable/unit，标量枚举按 choices。object_tags 展开指定对象，offset/limit 分页对象，property_offset/property_limit 分页属性；完整检查保存在 inspection_path。缓存数值不是新仿真结果。更新结构化规格须给实际 type；type/x/y 不改变已有对象或拓扑。原生属性更新清除旧结构化规格标记。


## 请求示例

只读当前需要的一项，均为合成数据，不是工程依据：

- [pump](examples/pump.json)
- [flash](examples/flash.json)
- [heat_exchanger](examples/heat_exchanger.json)
- [benzene_toluene_column](examples/benzene_toluene_column.json)
- [wgs_conversion](examples/wgs_conversion.json)
- [wgs_equilibrium](examples/wgs_equilibrium.json)

生成的请求、模型、校核和成果已有记录。需要具体结果用计算 ID、view=selected 和结果 JSON Pointer；失败再读收敛诊断，发布时再读发布说明。
