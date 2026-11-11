# 工程数值选取与缺项补齐

适用于进料、物性、收率、动力学与传递参数、设备效率、尺寸、公用工程和设计条件等输入。目标是保持用户约定的设计范围及深度，并使每个选值可追溯。价格沿用 `chem_prices/chem_price_save` 的真实报价保护和估值流程。

## 选取顺序

1. **用户数据优先**：查用户原话、文件、题设和本项目已有依据，保留原值、单位、条件及定位。题设假定也具有用户优先级，另标 `assumed=true`。先检查单位、组成基准、操作／设计条件、物理可行性；明显矛盾的数据保留并说明排除原因，可继续采用暂定替代值开展设计。
2. **适用资料与已有数据推算**：缺项先查共享资料库，再按具体数值广泛查找环评、官方／厂家资料、手册、论文、物性数据库及相似装置。按工况、组分、规模、技术路线和时间口径筛选，必要时交叉核对；不得仅看搜索摘要、只找到一个不适用数值就结束，或反复搜索而不推进。根据现有数据能由物料／元素／能量平衡、关系式、关联式、拟合或尺度换算得到的数值，同时考虑计算推理。资料与推算同级，按适用性择优，保留差异和选取理由。记录实际检索渠道、原文位置、公式、实际输入及引用版本；未实际搜索不能写成“广泛查证”。
3. **合理假设兜底**：上述方法仍不能补齐时，由当前 AI 提出物理合理、与流程一致的工程假设。说明为什么取此值、适用范围、不确定性及后续替换方式；重要假设安排上下界或替代情景并计算敏感性。缺少独立数据时如实记录“尚未标定”，继续完整设计。

推算使用了假设输入，或需要未经验证的外推／相关性假设时，结果保留假设属性，不能因经过一次计算就升级成有独立数据支持的输入。用户给定的假设仍优先于其他渠道数据，不能混同于实测数据。

**不得以数据缺失为由放弃设计、减少装置／设备覆盖或降低约定模型深度。** 保持要求清单、PFD、设备清单及进出口覆盖；用暂定参数完成原定计算。假设只补齐输入，不自动授权改变产品指标、工艺路线或交付范围。软件确实不支持的能力如实说明，并继续完成可实现部分和替代计算，不伪造原生能力、求解成功、标准符合性或专业审核。守恒、收敛、模型适用性按实际检查结果报告，不能为了“完成”而把失败改为通过。

## `chem_basis_select`

真实 MCP 工具签名：`chem_basis_select(project_id, request)`；对应内核 `POST /v1/projects/{id}/basis/select`。

工具只执行单位检查、优先级选取与持久保存。检索、公式计算、模型计算和适用性判断由当前会话与相应实际工具完成；本工具不执行公式、不验证来源真实性，不产生仿真计算记录。`evidence_verified=false` 保留这一边界。

请求指定工程对象 `kind/key/title`、`expected_revision`、更新 `reason` 和 `quantities`。新对象版本用0；新流程用 `chem_records(section="objects",key="basis/feed")` 定向读对象版本，旧项目也可读完整项目。选取后数值可由 DWSIM `basis_keys` 关联。候选、决策和历史由程序保存；不再让 AI 抄写另一套登记脚本。新流程探索试算无需先填依据，正式采用时补齐必要依据。

## 同来源批量简写

普通参数共享来源、条件、检索说明及理由，保存一次：

```json
{"kind":"basis","key":"feed","expected_revision":0,"reason":"采用题设参数",
 "source_groups":{"given":{"source_type":"user","source":"用户题设表1","conditions":"300 K，绝压",
   "search_summary":"用户已提供适用数据，无需补查；未做外部搜索",
   "rationale":"按用户指定工况采用","applicability_reason":"本情景题设","assumed":true}},
 "quantities":[{"key":"flow","value":2,"unit":"kg/s","source_group":"given","critical":true},
               {"key":"tolerance","value":0.00001,"unit":"dimensionless","source_group":"given","role":"solver"}]}
```

已有来源组可直接引用；更新组须传该组 `expected_revision`。每个量固定所用来源组版本；组更新使依赖结果过期，旧版仍能从 history 查询。逐项特殊来源、条件和推算链写 `overrides`；改变既有候选使用新 `candidate_id`，不能改写原候选。需要在同级采用新值时给 `preferred_candidate_id`，排除旧工况候选给 `exclusions` 和具体理由；冲突解释写 `selection_reason`。复杂多候选继续用完整格式。

`role=process/solver/geometry` 区分工艺、求解控制、展示几何。简写默认 `critical=false`；仅重要工艺假设设 `critical=true` 生成敏感性要求，所有假设仍保留属性。完整旧格式默认行为保持兼容。推算始终保留公式、实际输入和假设传递，不能因简写省略推算链。

每个数值提供：

| 字段 | 含义 |
| --- | --- |
| `key/unit/conditions` | 稳定数值标识、统一目标单位、相态／温压／组成／规模／设计口径；口径变化用新 key 或情景对象 |
| `candidates` | 新增候选列表；已有候选自动保留，候选 `id` 一旦保存不能修改，修正用新 id |
| `search_summary` | 实际查找或直接取得数据的经过及结果；没有用户数据、资料和可用推算时说明假设的形成过程 |
| `selection_reason` | 本次采用依据、同级冲突处理及适用性比较 |
| `preferred_candidate_id` | 可选；只允许在最高可用优先级内择优。新用户值替换旧用户值时显式指定 |
| `exclusions` | 可选；`{"候选id":"具体排除原因"}`。排除后仍保留原候选和历史；误排除可填新的候选id重建依据 |
| `minimum/maximum` | 可选；目标单位下、有依据的物理／工况适用边界，越界候选保留并排除 |

候选填写 `id/value/unit/source_type/source/conditions/rationale/applicability_reason`。`source_type` 为 `user/reference/derived/assumption`；`applicable=false` 时解释不适用原因。`low/high` 可保存原候选单位下的不确定性范围；上下限须包含采用值。标识出表压／绝压、质量／摩尔基准等差异，工具不会仅凭文字自动换算这些口径。

`derived` 另填 `derivation={method, formula, inputs, calculation_id?}`。每个实际输入含 `key/value/unit/source_type/source/assumed`，其中任何一个为假设，结果继承假设属性；上游推算含假设也必须传递 `assumed=true`。`calculation_id` 可关联本项目实际记录；校核失败的计算不能采用，DWSIM 来源还必须同时满足 `solved=true` 和 `validation.passed=true`。无工具计算记录的工程推理须保留完整推理链，不能杜撰计算ID。

自动规则：用户优先级0；适用资料／无假设的推算优先级1；AI假设及含假设推算优先级2。同级没有显式选择时优先保留已选候选，否则按候选提供顺序选择，因此首次提交同级候选按适用性排列。每个候选记录换算值、可用性和排除理由。不相容单位、非法偏好、重复标识或无可用候选会明确报错；当前会话据此修正输入、继续推算或补上合理假设，保留该设计项。

更新自动保留对象其他数值、属性和候选，不用低优先级数据覆盖高优先级数据。普通 `chem_object` 及 DWSIM 发布不能改掉已受选取机制管理的数值；发布实际操作结果时使用独立 key。普通旧数值首次纳入时，提供 `id=existing` 的匹配原值候选并补齐其来源；保留旧用户／假设属性。

旧对象只有空值占位时，可直接以资料、推算或假设补齐；旧记录保存在 `selection.legacy_quantity`。已有实际数值但单位等信息不完整时，明确报出缺项，先补齐原记录或建立新数值 key 继续设计，避免默默改写原始数据。

`assumed=true` 的结果标为 `origin=assumption`，返回 `sensitivity_required=true` 和 `pending_review`；这不阻止继续设计。`chem_review` 汇总为待标定提示，自动完整性和专业审核仍独立。数据修订使当前自动校核和成果包引用失效；按受影响的依赖重新计算、校核和出图。

## 调用例：用户流量优先

以下数值仅为机制示例，不能直接用作真实项目参数。

```json
{
  "project_id": "实际项目ID",
  "request": {
    "kind": "basis", "key": "feed", "title": "进料设计依据",
    "expected_revision": 0, "reason": "保留用户负荷并记录其他候选",
    "quantities": [{
      "key": "mass_flow", "unit": "kg/s", "conditions": "基准工况；进料总质量流量",
      "search_summary": "用户直接提供基准流量；另列演示假设候选，未进行外部搜索",
      "selection_reason": "用户数据适用于本工况，优先采用；统一换算为kg/s",
      "candidates": [
        {"id":"user-feed", "value":36, "unit":"t/h", "source_type":"user",
         "source":"用户原话：进料36 t/h", "conditions":"基准工况；总质量流量",
         "rationale":"直接采用题设负荷", "applicability_reason":"与当前基准负荷口径一致"},
        {"id":"fallback-feed", "value":8, "unit":"kg/s", "source_type":"assumption",
         "source":"机制演示假设，不是实际项目数据", "conditions":"基准工况；总质量流量",
         "rationale":"仅演示已有用户值时不采用较低优先级假设", "applicability_reason":"仅作为备用候选"}
      ]
    }]
  }
}
```

实际返回选值 `10 kg/s`、`source_type=user`，并保留原始 `36 t/h`。若只有假设候选，则选假设并记录待标定项。后续资料或用户数据到达时以当前版本追加候选，重新选取并沿依赖重算，原假设和旧结果仍可追溯。
