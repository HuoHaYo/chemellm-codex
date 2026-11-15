---
name: chemellm-references
description: 用 ChemELLM 检索、归档和引用化工标准、工程文献、原件、图表及扫描页。需要可追溯工程资料时使用；普通文件阅读不必建立工程资料库。
---

沿用已有项目 ID；独立资料任务可 `chem_project_init(task_kind="research")`，不建立工艺对象计划。按[原件与引用](references/references.md)保存原件→读相关部分→固定引用；有复用需要才整理，不先改写全文。

已知工具直接调用 `chem_reference_*`；检索参数用 `chem_tools(tool=...)`，用途不明用 `view="summary",category="references"`。只读相关页或段落，保留版本、来源及适用条件。

图像可按原件、页码、区域和哈希引用；确实引用图中文字才转录并核对。摘要不能冒充引句，未知标准状态不能写成现行或符合。用户数值优先，解释冲突和适用性；数值选取读[依据规则](../chemellm-engineering/references/numerical-selection.md)。

已有原件、快照和引用直接复用，不为不同技能复制资料。版本冲突先重读再合并。资料副本留内部；约定的研究报告可直接交付，按[共同流程](../chemellm-engineering/references/efficient-workflow.md)审核及按需打包。
