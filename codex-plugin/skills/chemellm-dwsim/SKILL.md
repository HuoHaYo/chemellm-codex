---
name: chemellm-dwsim
description: 用本地 ChemELLM Codex 插件与 DWSIM 建立或修改稳态流程、诊断收敛、运行多工况并发布结果。涉及 DWSIM 原生模型或明确稳态流程仿真时使用。普通物性计算使用通用技能。
---

直接用本插件的 `chemellm` MCP 沿用已有项目。新任务 `chem_project_init(task_kind="simulation")`，已有任务读摘要。软件就绪检查只做一次，失败或环境变化再查。数据路径以工具返回为准。

先读[基础建模](references/basics.md)。用 `chem_tools(tool="dwsim")` 定向读 schema；发现用途时用 `view="summary",category="dwsim"`。按真实规格 check 后求解，程序自动记录，不复制整套台账。

只加载当前所需主题：

- 严格塔、循环、反应或物性参数：[分离与反应](references/units.md)。
- 失败、校核或初值问题：[收敛诊断](references/convergence.md)。
- 原生图形：[布局](references/layout.md)。
- 扫描、取消和续算：[多工况](references/studies.md)。
- 采用、参考比较和设备表：[发布](references/publish.md)。
- 请求示例按基础说明链接的单个 JSON 读取。

保留单位、绝压、工况和来源；用户数值优先，推算保留公式和输入，重要假设继续传递。缺数按[数值规则](../chemellm-engineering/references/numerical-selection.md)补齐，不删除约定设备或降低模型深度。缺软件按[依赖规则](../chemellm-engineering/references/dependencies.md)。

计算目录读 `chem_calculations(view="summary")`；已知计算用 `view="selected",result_path="/validation"` 或具体字段。超长返回按提示细分，不凭截断抄数；完整诊断按需展开。沿用已有多工况任务状态和 ID。

正式采用须同时 `solved=true`、`validation.passed=true` 并关联必要依据；缓存、布局或守恒通过不等于专业验收。交付遵循[共同流程](../chemellm-engineering/references/efficient-workflow.md)，按需打包。
