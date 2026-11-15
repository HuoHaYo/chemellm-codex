# 采用、参考比较与交付


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

原件不覆盖，输入限本仓库/数据目录/DWSIM samples，单文件25MiB。内部保存输入JSON、结果JSON、物流CSV、原生模型及输入副本；过大结果用chem_calculations读完整记录，检查读inspection_path。超时终止子进程，已配置模型、请求、日志和基准保留，不伪造结果。最终交付按需求提供物流表、报告及原生模型；不把原始JSON、日志和输入副本加入交付包。需要ZIP时对实际入包文件逐一SHA256验证；运行数据和原生模型不进Git或插件发行包。

真实回归见scripts/test_dwsim_steady.py，缺少引擎不能算验收通过。覆盖水蒸汽/PR烃类/NRTL、热工分离/严格塔/循环/反应/多工况/发布数据表；独立预期包括解析守恒、CoolProp水焓、塔稳定基准及NIST气相WGS平衡参考点。test_dwsim_study_state.py用确定性状态机验证取消和中断恢复，不冒充真实引擎精度测试。
