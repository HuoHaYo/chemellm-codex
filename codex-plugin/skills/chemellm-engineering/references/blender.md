# Blender 三维演示

先用 `chem_dependencies(dependency="blender")` 检测后台、glTF 和 Eevee。含 CAD 的场景另检测 FreeCAD；参数化场景不要求 FreeCAD。已有安装直接复用，缺少软件沿用按需安装流程。

新流程 `chem_project_init(task_kind="scene",...)` 后即可试做，调用 `chem_tools(tool="export_blender")` 读取 schema，再 `chem_execute(project_id, tool="export_blender", args=...)`。要求与计划可以初始化时一并保存；展示几何不逐项重复填依据台账。旧项目保留阶段门禁。输入必须显式列出 `items`，不自动填入虚构厂区。

每项填写唯一 `id`、`kind`、`source`；`source` 说明尺寸/位置来源，假设明确标明。可用 `reference={"key":"equipment/T-101","revision":1}` 固定本项目对象，版本不符时重读对象并确认映射，不忽略冲突。生成不会把场景尺寸自动写回设计依据。

`units` 为 `mm`（默认）或 `m`，右手系 Z 向上；`position` 为三维坐标，`rotation_deg` 是 XYZ 欧拉角度。参数化设备基点为底面中心。管道、道路、围墙的 `points` 是局部坐标，再应用整体位移和旋转。

| kind | 必填几何 |
| --- | --- |
| column / vessel / tank | diameter, height |
| horizontal_vessel / exchanger | diameter, length，轴线沿局部 X |
| pump / box / building | length, width, height |
| pipe_rack | length, width, height；bays 默认 4 |
| pipe | diameter, points（至少两点） |
| road | width, points |
| fence | height, points |
| ground | length, width；可选 height |
| cad | path，支持 STEP/STP/FCStd；可选 object_names 指定 FCStd 对象 Name |

CAD 路径使用 MCP roots 或 `--allowed-root` 允许的任务目录，也兼容当前仓库与数据目录；使用源模型原点和毫米几何，再应用场景变换，不重复按 `units` 缩放 CAD。读取已保存静态形状，不修改原件、不复算 CAD 参数历史；复杂文档可用 `object_names` 指定最终对象。CAD 外观采用统一材质，不保证源模型的完整装配语义和材质。

默认输出 scene.blend、scene.glb、1280×720 preview.png，以及输入、来源副本、固定对象版本、运行脚本和报告。`render` 可调整图片尺寸、labels、font_path；手动相机用 `camera={"position":[...],"target":[...],"lens_mm":45}`。中文标注需本机字体；文字转成网格以便 GLB 查看。GLB 位号及来源保存在 extras；演示配色不视为工程标识规范。

报告检查相机是否覆盖设备，人工查看 PNG 确认标注和布局。失败先读诊断目录，不将部分输出当作成功成果。新导出保留旧成果，清除旧校核和成果包缓存。`chem_bundle` 在 `scenes/<id>/` 保持文件相对结构，可按其中 reproduce.md 重新生成；单文件打包上限 256 MiB，超限列入 manifest 缺项。

完整虚构示例在本仓库 `docs/examples/blender-plant.json`；所有尺寸均是演示假设。没有执行碰撞、检修空间、安全间距或施工配管审核，渲染成功不能代替工程审核。
