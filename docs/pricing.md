# 本地价格台账

设备、安装、管道、仪表电气、土建、公用工程、设计、预备费，以及原料、能源、催化剂、人工、维修和环保费用使用同一台账。每个项目独立保存，不访问相邻 DSH 项目。

## 保存与更新

1. `chem_prices(project_id)` 返回 `prices.revision`、`items`、`pending_review` 和真实本地路径。
2. `chem_price_save(project_id, items, expected_revision, reason, mode="upsert")` 按稳定 `key` 新增或更新完整行，其他行保留。已有假设可以用真实价格替换；工具禁止用假设覆盖真实价格。
3. `mode="fill_missing"` 只接受假设行并跳过已有 key，用于 AI 按项目条件补充缺价项；此模式不会重置已有报价。
4. `chem_price_history(project_id, key?, offset=0, limit=50)` 分页返回历次增删改的前后完整值、UTC 记录时间、操作者和理由。`key` 可筛选单项。

更新必须传入刚读取的 `expected_revision`；发现手工编辑或其他更新后返回版本冲突，AI 需重新读取合并。重复保存相同内容不新增版本。稳定 key 表示一项费用，例如 `equipment-E-101`、`installation-E-101`；设备位号相同但费用范围不同须用不同 key。多家供应商方案可分别留存，但汇总投资时只选采用方案。

价格登记不受工艺计算阶段约束，收到真实价格即可保存。工具不从外网自动询价，也不提供内置的通用设备价格。缺项估值由 AI 根据项目条件生成并解释，系统仅负责校验、保存及提示待核对。

## 可编辑文件

默认目录为 `data/projects/<项目ID>/prices/`，工具返回的 `prices.files` 是当前环境下的绝对路径：

- `prices.json`：UTF-8 JSON 当前表，用户可用文本编辑器直接增删改 `items`。`schema_version` 和 `revision` 由系统维护，手工编辑时保持原值。不要同时用多个旧版本的编辑窗口覆盖保存。
- `history.json`：系统维护的历史，记录每次修改前后的完整行；用户可读，不应手工改写。直接编辑当前表的变更在下次价格读取或项目操作时导入，操作者标为 `local_edit`。两次读取之间的多次保存只能记录最终可见差异；需要逐次留痕时，每次修改后调用 `chem_prices`。
- `.pending.json`：中断恢复日志，正常提交后自动移除。写入中断时先读台账恢复，不应手工删除日志再重试。

当前表被删除时，可从历史恢复；删除某一项应在当前表 `items` 中删除对应行，历史仍保留。格式或数值无效时，系统返回错误并保留原文件，不静默丢弃编辑。文件可带 UTF-8 BOM。

当前表外形为：

```json
{
  "schema_version": 1,
  "revision": 0,
  "items": []
}
```

## 每项字段

| 字段 | 含义 |
| --- | --- |
| `key`、`category`、`title` | 稳定标识、费用类别、可读名称 |
| `equipment_tag` | 设备位号，设备价格必填；其他费用可关联位号或留空 |
| `model_spec`、`material` | 型号/规格、材质；未知留空，自动列为设备待补项 |
| `design_pressure`、`design_temperature` | `{ "value": 数值, "unit": "MPa" 或 "degC" }`；未知为 `null`，压力表压/绝压另在条件中说明 |
| `quantity`、`quantity_unit`、`unit_price` | 数量、计价单位、单价；数量须大于 0、单价非负。整项总价使用数量 1、单位“项”，在条件中解释范围 |
| `currency` | 三字母币种，例如 `CNY`、`USD` |
| `cost_basis` | `one_time` 一次性、`annual` 每年、`per_batch` 每批、`other` 其他期间；详细口径写入条件 |
| `conditions` | 地区、价格基期、适用工况、计价范围和期间等 |
| `tax`、`freight` | `{ "status": "included/excluded/unknown", "amount": 金额或null, "rate": 小数税率或null, "note": "口径说明" }`；金额与主项同币种，数量/整项口径写明；不要重复计费 |
| `installation_scope` | 安装包含/排除的工作范围；确实不适用时填写“不适用” |
| `source` | 每次新增或更新必填本次价格来源，如竞赛题目及页码、工厂/供应商报价、用户原话或估算依据；来源不详时如实注明待补 |
| `quote_date`、`valid_until` | `YYYY-MM-DD` 或 `null`；未知留空，不能用记录时间冒充报价日期 |
| `origin` | `quotation` 真实价格信息，`assumption` 假设价格；题设假定与 AI 暂估的区别写入备注 |
| `rationale` | 更新理由或估算方法、工程判断、适用性及不确定性；必填 |
| `assumed_fields` | 实价记录中另外暂估的字段，例如 `["quantity", "tax.rate"]`；理由中逐项解释 |
| `estimate_low`、`estimate_high` | 可选的单价估值范围，须同时给出且包含采用单价 |
| `notes` | 用户可读、可编辑的价格备注，用自然语言说明题设假定、某工厂报价或 AI 暂估，以及适用背景 |

`category` 支持 `equipment`、`installation`、`piping`、`instrument_electrical`、`civil`、`utility`、`engineering`、`contingency`、`raw_material`、`energy`、`catalyst`、`labor`、`maintenance`、`environmental` 和 `other`。

`pending_review` 自动汇总假设价格、假设字段、关键元数据缺项和超过有效期的报价。过期报价保留原值及真实来源状态，等待确认；不会自动改成新报价或假设。

例如，题设价可记录 `source="某竞赛题目第 8 页"`、`notes="题目假定价格，用于该题经济评价"`；工厂价可记录 `source="用户转交的某工厂报价单第 2 页"`、`notes="该工厂报价，供货范围及适用工况见条件"`。以上仅为字段填写示例。每次更新同步填写本次来源和备注；引用时由 AI 阅读来源、备注和条件，结合当前任务判断，无固定来源优先级或相似度排序。历史记录保留历次来源和备注。

## 审核和经济评价

台账版本变化会清空旧自动校核和当前成果包引用，旧文件本身仍保留。`chem_review` 列出价格待核对项；经济评价记录绑定执行时的价格版本，价格更新后需重新汇总并运行评价。自动完整性检查不会将假设解释为真实报价，也不代表专业审核通过。

当前经济评价仍接收总投资、年收入和年成本。AI 应先按币种、税费、运费、安装范围、费用期间与采用方案汇总，避免重复计费；系统不自动换汇，不把一次性投资与年成本直接相加。历史版本关联只表示评价执行时读取过该版本，不能证明输入总额已经与台账逐项核对。

`chem_bundle` 同时收录当前表和完整历史，二者均列入清单并提供 SHA256；`.chem/<项目ID>/prices/` 也保存只读快照。台账属于运行数据，不纳入 Git 或发行包。
