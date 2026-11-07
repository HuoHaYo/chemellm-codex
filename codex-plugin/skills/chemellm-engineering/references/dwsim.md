# DWSIM 稳态建模与结果使用

先 `chem_dwsim_status()` 检测本机 DWSIM Automation3。当前以 Windows .NET Framework 和 DWSIM 9.0.5 的实际 API 核对；`ready` 只表示环境可用。缺失软件按 [安装流程](dependencies.md) 展示理由和具体方案，取得用户同意后安装。DWSIM 不随插件分发，不增加模型 API。

`chem_dwsim(project_id, request)` 与 `chem_execute(tool="dwsim", args=request)` 共用内核。`chem_tools(tool="dwsim")` 返回完整 JSON Schema。流程是：关联依据 → check → 补齐规格 → 求解 → 独立校核 → 参考数据比较 → 多工况 → 显式发布。

[完整可复算请求](dwsim-examples.json) 提供泵、两相闪蒸、双物流换热器、苯—甲苯严格塔、转化率及平衡反应六例；选择 examples 中对应的一项作为 request。先改为 action=check 检查，再 create 求解。数据均为合成条件，需要按实际项目补充来源与依据。

## 动作与模型

| action | 输入 | 行为 |
| --- | --- | --- |
| catalog | 可选 compound_query、offset/limit | 实际组分名、CAS、物性包和对象类型 |
| inspect | flowsheet_path；可选 object_tags | 只载入缓存，读端口、标量参数、属性ID与单位 |
| check | model 或 flowsheet_path；文件可带 updates | 只检查，不求解、不创建正式计算、不改原件 |
| create | model | 创建、求解、保存独立原生模型和计算记录 |
| run | flowsheet_path、updates、initialization、property_packages | 复制原件、修改、求解、另存新文件 |
| layout | flowsheet_path；可选 layout、output_format | 编辑原生图形，另存模型、预览和布局报告；不求解、不创建计算记录 |

status/catalog/inspect/check 不要求先完成依据阶段。create/run 遵循项目阶段要求。`basis_keys=["basis/feed"]` 关联已有工程依据。缺失来源或尚未支持的自由度判断显示待核对；明确的连接、进料、规格缺项定位到位号。check 的 `preflight.ready_to_solve` 不代表模型已经求解或通过全部自由度分析。

`model` 必填 compounds/property_package/objects/connections。进料明确 temperature_k、pressure_pa、mass_flow_kg_s 和全部组分 mole_fractions，和为1，不自动归一化。温度K，压力绝压Pa，质量流量kg/s，摩尔流量mol/s，热流/轴功kW，质量焓kJ/kg。连接 source/target 的端口从0开始，先读取真实端口名和类型，不猜能量口。

原有 parameters/properties 保持兼容。结构化 spec 与同参数重复或额外目标冲突会拒绝。原生属性按 inspect 的 writable/unit，标量枚举按 choices。object_tags 展开指定对象，offset/limit 分页对象，property_offset/property_limit 分页属性；完整检查保存在 inspection_path。缓存数值不是新仿真结果。更新结构化规格须给实际 type；type/x/y 不改变已有对象或拓扑。原生属性更新清除旧结构化规格标记。

## 原生流程图布局与 AI 编辑

新建模型需要可读布局时显式提供 `layout={}`，或在 `create/run` 中提供分区配置；省略时兼容原有坐标行为。已有文件先 `inspect(view="layout", flowsheet_path=...)`，读 `graphics.nodes/edges/metadata`、原生 PNG 和 `inspection_path` 中的完整图形，再调用 `layout` 编辑。对象通过原生 `name` 或唯一 `tag` 引用。分区语义由 AI 根据实际工艺资料确定，自动布局负责拓扑排列。

```json
{
  "action": "layout",
  "flowsheet_path": "data/example.dwxml",
  "output_format": "dwxml",
  "layout": {
    "mode": "auto",
    "groups": [{"id": "heating", "title": "进料与加热", "members": ["HT-101"]}],
    "pinned": [],
    "nodes": []
  }
}
```

`groups` 成员可只列设备，物流归入上游设备分区，进料归入下游分区；剩余连通分量另建分区。分区可给 `x/y` 指定内容起点；手动模式移动成员，固定对象保留原位。省略分区配置时沿用嵌入文件的分区；新的非空列表替换现有分区定义。

局部精调：`layout={"mode":"manual","nodes":[{"object":"HT-101","x":500,"y":300,"rotation":90,"width":70,"height":100,"flipped_h":false}]}`。位置须同时给 x/y；尺寸为画布尺寸，旋转支持 0/90/180/270，另支持 `flipped_v`。自动模式中显式位置也作为固定约束。`pinned` 固定当前位置，省略时沿用保存的固定对象，显式 `[]` 清除旧约束；冲突返回重叠对象。默认间距 `grid=20, layer_gap=220, node_gap=100, group_gap=240`，单位为原生画布单位。

`layout` 不接受工艺 updates、物性或初始化。它保留原文件，将允许修改的图形字段、原生分区框和带中文字体 CSS 的原生 HTML 文字、`ChemELLM_layout_v1` 元数据写入新文件，再真实载入新文件检查。工艺及连接数据保护见 `protected_data_unchanged`，重开检查见 `saved_reload_verified`。布局不返回新的 `solved/validation` 结论；需要计算验收时再对保存后的文件执行 `run`。

`saved_flowsheet` 是新原生文件，`layout_path` 是完整前后图形与质量报告，`previews` 是 DWSIM 原生 Skia 渲染图。大分区按画布范围裁切为细节 PNG，总览用于定位。`layout_quality.before/after` 列出符号及标签包络重叠、连线交叉、穿过设备和长连接；`passed` 仅表示包络无重叠，不等于全部走线理想或工艺验收通过。查看预览后以手动模式继续精调，保留未解决问题。

连线由 DWSIM 原生重建，不提供无法稳定保存的任意折点。分区图形不增加计算设备或改变端口。默认输出格式：layout 沿用输入，create/run 为 dwxmz；用 `output_format` 显式选择 dwxml/dwxmz。DWXML 去除节点间排版空白以控制尺寸，数值和文本保留；超过 25 MiB 时明确报错，可显式选择 dwxmz。

## 热工、分离和循环配置

| type | spec.mode | 必要字段及可选项 |
| --- | --- | --- |
| Heater/Cooler | outlet_temperature / heat_duty | temperature_k / heat_duty_kw；pressure_drop_pa、efficiency |
| Pump/Compressor | outlet_pressure / pressure_change | pressure_pa / pressure_change_pa；efficiency（0–1） |
| Valve | outlet_pressure / pressure_change | pressure_pa / pressure_change_pa |
| HeatExchanger | ua | area_m2、overall_coefficient_w_m2_k；两侧压降、flow_direction |
| HeatExchanger | hot_outlet_temperature / cold_outlet_temperature | temperature_k，明确哪侧出口温度 |
| Vessel | adiabatic / tp | tp 需要 temperature_k、pressure_pa |
| ShortcutColumn | shortcut | light_key、heavy_key、light_key_in_bottoms、heavy_key_in_distillate、reflux_ratio、pressure_pa |
| DistillationColumn | column | column 嵌套配置，见下例 |
| Splitter | split_ratios | ratios，2或3路、非负且和为1 |
| OT_Recycle | recycle | maximum_iterations、acceleration、温压流量组成容差 |
| RCT_Conversion/RCT_Equilibrium | reaction | reaction_set、reactor_mode；指定出口温度时填写 temperature_k |

热工单元连接必要热流/轴功。简捷塔有再沸器输入、冷凝器输出能量流，和严格塔端口不同。严格塔按产品/进料位号调用专用连接 API，feed_stages 从1开始并包含冷凝器和再沸器。

可复算泵模型（合成接口例子，不是设计依据）：

```json
{"action":"create","model":{
 "compounds":["Water"],"property_package":"Steam Tables (IAPWS-IF97)",
 "objects":[
  {"tag":"F","type":"MaterialStream","temperature_k":300,"pressure_pa":300000,"mass_flow_kg_s":1,"mole_fractions":{"Water":1}},
  {"tag":"P-101","type":"Pump","spec":{"mode":"outlet_pressure","pressure_pa":600000,"efficiency":0.75}},
  {"tag":"P","type":"MaterialStream"},{"tag":"W","type":"EnergyStream"}],
 "connections":[{"source":"F","target":"P-101"},{"source":"P-101","target":"P"},
  {"source":"W","target":"P-101","target_port":1}]}}
```

苯—甲苯严格塔单元规格（Raoult's Law；F为365K、300000Pa、1kg/s、等摩尔，F→T-101→D/B）：

```json
{"tag":"T-101","type":"DistillationColumn","spec":{"mode":"column","column":{
 "stages":12,"top_pressure_pa":101325,"bottom_pressure_pa":110000,
 "feed_stages":{"F":6},"distillate_stream":"D","bottoms_stream":"B",
 "condenser":"Total_Condenser",
 "condenser_spec":{"type":"Stream_Ratio","value":3,"unit":""},
 "reboiler_spec":{"type":"Product_Molar_Flow_Rate","value":5.87,"unit":"mol/s"},
 "maximum_iterations":300,"solver_tolerance":0.000001,"external_tolerance":0.00001}}}
```

本机稳定基准塔顶苯摩尔分数约0.9693。塔规格还支持质量流量、组分分数/回收率、温度、负荷；组分规格填 component。只对明确支持的组合检查冲突，不宣称通用自由度分析。更严的原生容差也可能触发引擎误差检查，失败需保留诊断，不暗改指标换取收敛。

循环初值在循环 MaterialStream 指定。recycle 支持 maximum_iterations、acceleration、temperature_tolerance_k、pressure_tolerance_pa、mass_flow_tolerance_kg_s、composition_tolerance。诊断保留引擎实际 Errors/Converged/IterationsTaken/IterationCount；即使返回0也不伪造迭代次数。`run.initialization` 是最多10个显式更新列表，每步应用并求解，最后应用 updates，记录每步状态和错误。

## 物性与参数

保留单一 property_package；另可声明 `property_packages=[{key,name,rationale,source,conditions,interactions}]`，单元用 property_package=key 指定。选择理由与来源随模型保存，结合 basis_keys 所指依据解释。参数表可在独立工况更新，用于模型对比。

二元参数含 compound1/compound2/model/parameters/source/conditions/notes。支持对应原生 PR、SRK、NRTL、UNIQUAC 表，必须匹配物性包。先读取实际成员和组分方向，再覆盖；已有反向记录时要求沿用返回方向，避免错换系数。结果区分 user_specified、native_database_or_default、missing_or_implicit_default；原生零值不能描述为验证过的实验参数。保存实际系数、comment、来源、条件、温度形式和单位；未知适用温度不自行推定。

本机9.0.5核对的形式：NRTL `tau_ij=(Aij+Bij*T+Cij*T^2)/(1.98721*T)`；UNIQUAC `tau_ij=exp((-Aij+Bij*T+Cij*T^2)/(1.98721*T))`。T为K，A/B/C分别为cal/mol、cal/(mol*K)、cal/(mol*K²)；NRTL alpha无量纲。PR/SRK为常数kij。参数声明的来源不自动证明其真实性或适用性。

## 校核与反应

`solved` 是原生引擎状态。`validation.checks` 每项有 status=passed/failed/not_checked、passed=true/false/null、原因和位号；守恒项有实际残差、单位与容差。总 passed 仅在适用必需检查全部完成且通过时为true，失败false，信息不足null。当前 schema_version=1；旧记录不追溯修改，没有该校核版本的旧结果需要重新运行后才能发布。

按单元和全流程检查质量、非反应组分质量、能量；内部物流不重复进入外边界，热/轴功从单元原生字段独立读取，不用焓残差反推。反应使用含生成焓的总焓及元素守恒。缺少必要数据/不支持单元显示未检查。零流量出口不要求未定义的组成归一化。容差随结果保存：relative=1e-5；mass_absolute_kg_s=1e-8；component_absolute_kg_s=1e-8；energy_absolute_kw=1e-4；element_absolute_mol_s=1e-7。判据 `abs(in-out)<=absolute+relative*max(abs(in),abs(out))`，可通过 validation_options 明确修改。

反应配置放 model.reactions 与 reaction_sets：

```json
{"reactions":[{"key":"shift","type":"Equilibrium",
 "stoichiometry":{"Carbon monoxide":-1,"Water":-1,"Carbon dioxide":1,"Hydrogen":1},
 "base_compound":"Carbon monoxide","phase":"Vapor","equilibrium":"Gibbs",
 "source":"填写反应/物性依据","conditions":"填写适用条件"}],
 "reaction_sets":[{"key":"wgs","reactions":["shift"]}]}
```

RCT_Equilibrium 单元配 `spec={mode:"reaction",reaction_set:"wgs",reactor_mode:"Isothermic"}`，连接气液出口。转化率案例用 RCT_Conversion，反应 type=Conversion、conversion=0.3（0–1）。反应物计量负、产物正；基准组分必须是反应物。平衡可用 Gibbs 或 Constant＋equilibrium_constant，不接收代码表达式。检查组分、元素配平和转化率范围；读取实际反应进度、组分转化率、平衡常数和热效应。动态、动力学CSTR/PFR和任意代码速率不在本版。

## 多工况

`chem_dwsim_study(project_id, request)` 对应 POST /v1/projects/{id}/dwsim/study：

```json
{"action":"start","request_key":"load-01","flowsheet_path":"<原生模型路径>",
 "cases":[{"key":"normal"},{"key":"high","updates":[{"tag":"F","mass_flow_kg_s":1.2}]},
          {"key":"low","updates":[{"tag":"F","mass_flow_kg_s":0.8}]}],
 "metrics":[{"key":"duty","path":"/unit_operations/@P-101/net_energy_input_kw","unit":"kW"}]}
```

cases 与 scan 二选一。扫描示例 `scan={tag:"F",field:"temperature_k",values:[300,310,320]}`，也支持pressure_pa、mass_flow_kg_s。reflux_ratio 扫描提供完整严格塔 object_template，冷凝器是Stream_Ratio，每次仅改该数值。每任务最多50工况，默认串行，单次超时120秒、范围5–900秒。

Metric.path为JSON Pointer，数组可用@位号；原生单位验证/换算，不能任意贴单位。denominator_path可计算同量纲比值（回收率等），unit为dimensionless或%。未知单位位置拒绝。

返回job_id后，用 action=status/cancel/resume＋job_id。取消在当前单次求解结束后停止；续算只做未完成工况。失败和failed_validation终态保留，不自动重跑；修正后新建任务。进程中断后关联已提交的计算，避免重复执行。相同request_key与相同请求复用任务，改变方案须新key。

基准副本及哈希、每工况独立原件/原生模型/计算/校核持久化；任务在data/projects/<id>/dwsim-studies/。启动或续算才运行，无后台监控。同项目写入互斥；状态和取消仍可调用。部分失败保留其他结果，只有校核通过且指标完整的点进入有效包络。输出CSV、JSON、每指标PNG曲线，表中保留失败点。

## 发布、参考比较与数据表

`chem_dwsim_publish(project_id, request)` 对应 POST /v1/projects/{id}/dwsim/publish。只接收明确选中的本项目且validation.passed=true的计算：

```json
{"calculation_id":"<计算ID>","case":"normal","objects":[
 {"kind":"equipment","key":"P-101","title":"进料泵","expected_revision":0,
  "quantities":[{"key":"operating_pressure","path":"/streams/@P/pressure_pa","unit":"MPa",
   "origin":"operating","conditions":"正常工况出口绝压","rationale":"工艺操作条件，机械设计另定"}]}],
 "references":[{"key":"pressure","path":"/streams/@P/pressure_pa","unit":"Pa","value":600000,
  "source":"合成规格，仅验收接口","conditions":"出口绝压","absolute_tolerance":0.1}]}
```

新对象expected_revision=0，更新给当前revision，冲突409；相同发布不重复建项，保留其他数量。映射origin为operating或simulation_load，存储来源为calculation。保留实际指针、原生/目标单位、条件、理由和工况；固定计算记录、原生模型哈希、运行器版本。操作温压和模拟负荷不自动变成机械设计温压或裕量；更新对象使旧自动校核失效。价格仍结合规格、来源和备注判断，不自动覆盖报价。

references保存参考值、计算值、偏差、来源和条件，可附本项目artifact_id/page。给出绝对/相对容差才判断参考点，否则为null。生成JSON和Markdown报告。没有独立参考数据保留验证缺口；题设/合成数据不能写成工厂实测验证，守恒通过不证明模型精度或专业审核。

现有equipment_datasheet的items可加显式对象映射：

```json
{"items":[{"tag":"P-101","equipment_type":"pump","project_object":{
 "key":"equipment/P-101","expected_revision":1,"atmospheric_pressure_pa":101325,
 "fields":{"operating_pressure_mpa":"operating_pressure"}}}]}
```

支持操作温度/压力、负荷、功率、面积、流量、扬程、压比字段并换算单位。数据表为表压、仿真为绝压，映射压力须明确atmospheric_pressure_pa。设计温压、材料和裕量由独立依据提供。数据表JSON保留来源和对象版本，DOCX/PDF备注计算ID。

## 文件与验收

原件不覆盖，输入限本仓库/数据目录/DWSIM samples，单文件25MiB。保存输入JSON、结果JSON、物流CSV、原生模型及输入副本；过大结果用chem_calculations读完整记录，检查读inspection_path。超时终止子进程，已配置模型、请求、日志和基准保留，不伪造结果。文件随成果包逐一SHA256验证，运行数据和原生模型不进Git或发行包。

真实回归见scripts/test_dwsim_steady.py，缺少引擎不能算验收通过。覆盖水蒸汽/PR烃类/NRTL、热工分离/严格塔/循环/反应/多工况/发布数据表；独立预期包括解析守恒、CoolProp水焓、塔稳定基准及NIST气相WGS平衡参考点。test_dwsim_study_state.py用确定性状态机验证取消和中断恢复，不冒充真实引擎精度测试。
