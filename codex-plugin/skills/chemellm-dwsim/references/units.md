# 分离、循环、反应与物性参数


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


## 反应配置
反应配置放 model.reactions 与 reaction_sets：

```json
{"reactions":[{"key":"shift","type":"Equilibrium",
 "stoichiometry":{"Carbon monoxide":-1,"Water":-1,"Carbon dioxide":1,"Hydrogen":1},
 "base_compound":"Carbon monoxide","phase":"Vapor","equilibrium":"Gibbs",
 "source":"填写反应/物性依据","conditions":"填写适用条件"}],
 "reaction_sets":[{"key":"wgs","reactions":["shift"]}]}
```

RCT_Equilibrium 单元配 `spec={mode:"reaction",reaction_set:"wgs",reactor_mode:"Isothermic"}`，连接气液出口。转化率案例用 RCT_Conversion，反应 type=Conversion、conversion=0.3（0–1）。反应物计量负、产物正；基准组分必须是反应物。平衡可用 Gibbs 或 Constant＋equilibrium_constant，不接收代码表达式。检查组分、元素配平和转化率范围；读取实际反应进度、组分转化率、平衡常数和热效应。动态、动力学CSTR/PFR和任意代码速率不在本版。
