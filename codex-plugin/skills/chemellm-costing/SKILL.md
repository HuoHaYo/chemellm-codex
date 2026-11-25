---
name: chemellm-costing
description: 用本地 ChemELLM Codex 插件保存设备及能源报价、费用估值、价格版本并做经济评价。涉及真实价格、工程成本或经济计算时使用。无费用要求的任务不启动价格台账。
---

用本插件的 `chemellm` MCP 沿用已有项目和设备标识。新任务 `chem_project_init` 一次初始化，不重建上游工艺模型。先读[价格规则](references/pricing.md)，真实报价及时保存，估值不能覆盖真实价格。

先 `chem_prices` 读取版本及来源，按设备或费用范围使用稳定 key；本次来源、背景、币种、计价期间和已知商务条件随价格保存。未知日期与规格不补造。冲突时重读合并，不重复提交旧版本。

缺价按当前规格、地区及基期做有依据暂估，标为 assumption 并说明范围、不确定性和假设传递。经济评价前处理待核对项，避免数量、税运安装费及费用期间重复计价。

用 `chem_tools(tool="economics")` 定向读参数；发现用途用 `view="summary",category="costing"`。工具自动记录输入和结果，不写第二套登记脚本；目录用摘要，数值用 `chem_calculations(view="selected",calculation_id=...,result_path=...)`。

价格或上游依据变化后遵守失效规则，不能重新审核消除过期。按[共同流程](../chemellm-engineering/references/efficient-workflow.md)关联计算、依据和成果；单份报告直接链接，按需打包。估值和自动校核不等于市场报价或专业审核。
