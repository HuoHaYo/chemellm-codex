---
name: chemellm-engineering
description: 用 ChemELLM 做通用化工计算、工程依据决策、项目衔接和成果交付。通用或跨专业任务使用；DWSIM、三维、资料和费用可直接使用专项技能。
---

使用现有 `chem_*` MCP，不另建服务或重复登记脚本。新任务 `chem_project_init` 一次初始化；已有任务读 `chem_project(view="summary")`。切换专业沿用项目 ID，不重新初始化。数据位置以 `chem_health` 为准，不读取 DSH 项目。

已知工具直接 `chem_tools(tool=...)`；用途不明才用 `chem_tools(view="summary",category="engineering")`。按真实 schema 试算，程序保存请求、版本、结果及成果。AI 只解释来源适用性、冲突、重要假设和参数变化。

数值保留单位、条件、来源；用户数据优先，资料或有依据推算其次，假设兜底并传递。缺数继续约定范围和模型深度。共同来源用来源组；选取数值时读[依据规则](references/numerical-selection.md)。

仪表/电气接线读[自控接线](references/automation.md)，定向查询 `qelectrotech`。出图或布置读[视觉规则](references/visual-standards.md)，核对图种、标准范围及跨格式表达。

记录用 `chem_records(section=...,key=...)`；计算目录用 `chem_calculations(view="summary")`，数值用计算 ID、`view="selected"`、`result_path`，不抄截断结果。更新项目、采用结果或登记外部成果时读[共同流程](references/efficient-workflow.md)。

正式采用明确计算、依据和成果映射后 `chem_review`。通过即可完成并链接文件，按用户需求和整体下载场景决定打包。自动校核不替代专业审核。失败先读状态再修正，成功须明确替代旧结果。旧项目显式请求才升级，不热更新运行会话。
