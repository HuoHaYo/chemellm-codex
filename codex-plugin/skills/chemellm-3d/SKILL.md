---
name: chemellm-3d
description: 用本地 ChemELLM Codex 插件生成或检查 Blender/CAD 三维设备、厂区布置和网页展示，核对连接、空间和文件一致性。DWSIM 流程图布局使用 DWSIM 技能。
---

用本插件的 `chemellm` MCP 沿用已有项目。新任务 `chem_project_init(task_kind="scene")` 后即可试做。展示几何与工艺设计参数分开，不逐项重建依据。保留尺寸单位、位置来源和假设，不凭空补入设备。

已知生成器用 `chem_tools(tool="export_blender")`、`export_freecad` 或 `cad_part` 查真实参数；发现用途用 `view="summary",category="3d"`。Blender 和 CAD 导入读[模型操作](references/blender.md)；没有 schema 的特殊工具按返回说明读取内核约定，不猜参数。缺软件按[依赖规则](../chemellm-engineering/references/dependencies.md)。

程序成果已有记录，不重复登记。外部模型、网页及必要资源用 `chem_artifacts_register` 批量固定版本，关联文件角色及相对资源依赖，按[共同流程](../chemellm-engineering/references/efficient-workflow.md)操作。

查看真实预览，按[视觉规则](../chemellm-engineering/references/visual-standards.md)检查转角、接管、设备职责及平立面一致性；外部报告保留外部声明身份。三维验收不强制工艺计算，也不代替布置专业审核。失败先查已有状态和诊断。

沿用同一项目及成果 ID，单份文件直接链接，整体下载才打包。原生模型按需求保留；过程 JSON、临时网格、日志、脚本和备份留内部，网页运行需要的 JSON、脚本及贴图作为资源保留。
