---
name: chemellm-engineering
description: 使用 ChemELLM 本地 MCP 做化工计算、DWSIM 稳态仿真、标准与参考资料归档引用、证据整理、工程对象与价格台账管理、自动校核和成果打包。适用于需要可追溯工程记录的化工任务。
---


使用本插件的 `chem_*` MCP 工具。新任务用 `chem_project_init` 一次建立任务类型、要求和交付项；已有任务先读 `chem_project` 摘要。`chem_health` 显示数据位置、内核版本、技能指纹及能力；数据只使用本仓库，不借用相邻 DSH 项目。

探索阶段直接按真实 schema 试算，先取得可复算结果。工具自动保存实际请求、版本、结果、诊断、校核和成果；AI 不再写重复登记脚本。默认读摘要，详情用 `chem_records` 按对象/计算/文件分页，或按 ID 读 `chem_calculations`。流程、映射与迁移见 [高效工作流](references/efficient-workflow.md)。

AI 只解释数据冲突、来源适用性、重要假设和参数变化。同来源参数用 `chem_basis_select` 的来源组与批量简写；保留用户数据优先、适用资料/已有数据推算其次、合理假设兜底的顺序，以及单位、条件、推算链和假设传递。缺数时继续完成约定范围和模型深度；围绕重要工艺假设安排敏感性任务。详细规则见 [数值选取](references/numerical-selection.md)。

按当前任务加载资料：
- 实际价格或缺价估值：[价格台账](references/pricing.md)，真实价格及时保存，假设不能覆盖真实报价。
- 标准、文献、图像：[资料引用](references/references.md)，保存原件→读取相关部分→固定引用，整理稿按复用需要生成。
- 稳态仿真：[DWSIM](references/dwsim.md)，失败诊断保持原定模型深度；正式采用须同时 `solved=true`、`validation.passed=true`。布局或缓存检查不等于求解。
- 三维模型：[Blender](references/blender.md)，查看实际预览并按模型、连接、空间、文件一致性验收。
- 缺少当前任务需要的外部工具：[可选依赖](references/dependencies.md)，沿用已有授权；新安装方案须先取得真实用户同意。

正式交付时明确采用的计算 ID、依据对象及成果文件映射，再 `chem_review`；按交付需要 `chem_bundle`，探索期间不逐次审核打包。外部软件成果用 `chem_artifacts_register` 批量登记；登记不等于独立验证其检查报告。自动校核与专业审核分开，`ready` 不代表工程标准符合性。

失败或中断后先读已保存状态与诊断，再修正重试；保留历史失败，成功结果须明确替代后才能用于交付。旧项目保持原流程，只有显式 `upgrade=true` 才迁移。不热更新正在执行的建模会话。
