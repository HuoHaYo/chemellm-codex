# Blender 化工厂三维演示

`export_blender` 使用本机 Blender 后台生成原生模型、GLB 和 PNG。支持设备、管道、管廊、道路、厂房、围墙和地面，并可与 STEP/STP、FCStd 的静态几何混合使用。无需额外 Blender MCP、常驻服务或 Blender 扩展。

接口和坐标约定见 [插件调用说明](../codex-plugin/skills/chemellm-engineering/references/blender.md)。默认使用毫米输入、米制模型、Eevee、1280×720，整次导出限时 600 秒。默认相机自动覆盖场景，可显式覆盖相机。场景、位号、来源写入模型与报告，GLB 导出对象 extras。

## 调用示例

先检测 `chem_dependencies(dependency="blender")`，保存项目要求、计划和设计依据，再读取 `chem_tools(tool="export_blender")`。通过既有执行入口调用：

```json
{
  "project_id": "项目ID",
  "tool": "export_blender",
  "args": {
    "units": "mm",
    "items": [
      {"id":"T-101","kind":"column","position":[0,0,0],
       "diameter":3000,"height":18000,"source":"演示假设，非实际设计数据"}
    ]
  }
}
```

[完整厂区示例](examples/blender-plant.json) 是虚构的演示输入，包含所有参数化类型。实际项目应按来源填写尺寸与位置，`reference` 可固定工程对象版本；版本不一致返回 409。

## 环境与 CAD

Blender 使用 `BLENDER_HOME`（安装目录或可执行文件）、PATH、Windows 常见安装目录以及 macOS/Linux 标准位置发现。要求 Blender 4.1+ 且后台、glTF、Eevee 能力检测成功；本次基准环境为 4.1.1。未验证的其他版本应执行本仓库专项测试。

CAD 导入另需现有 FreeCADCmd。先把文件放入本仓库或当前数据目录，提供 `kind="cad"` 和 `path`；原件按导出保存副本与哈希。FreeCAD 在独立配置下提取静态面几何，保留组件名称和内部位置，采用统一演示材质。仅参数化场景无需 FreeCAD。支持中文、空格路径；不修改源文件、不执行源文档的参数复算。

软件缺少时沿用 [依赖安装流程](dependencies.md)，不会自动安装或升级已有软件。插件不捆绑 Blender/FreeCAD。

## 成果与验收

每次输出独立成果目录，登记 blend、GLB、PNG、输入、CAD 来源、脚本、日志和报告。全部必需输出校验成功才登记；失败保留 failure.json 和日志。MCP 返回本地绝对路径与 SHA256，原生模型和 GLB 使用 PNG 预览。

`chem_bundle` 在 `scenes/<导出ID>/` 保留相对结构，按 reproduce.md 可重新生成；每个文件单独校验哈希。上传仍限 25 MiB，成果打包单文件上限独立为 256 MiB，超限会在 manifest 中标记。

专项验证：`python -X utf8 scripts/test_blender.py`。真实测试使用独立临时项目，覆盖原生重开、GLB 再导入、毫米/米一致性、混合 CAD、源文件哈希和打包；缺软件时对应真实测试明确跳过。虚构厂区视觉验收产物保存在忽略目录 `output/blender-qa/`。

三维结果用于方案展示，不执行碰撞、检修空间、安全间距、配管等级或施工图审核。后台生成不联动正在打开的 Blender 场景，不生成视频。
