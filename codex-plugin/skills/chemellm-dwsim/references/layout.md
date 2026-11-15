# 原生流程图布局


新建模型需要可读布局时显式提供 `layout={}`，或在 `create/run` 中提供分区配置；省略时兼容原有坐标行为。已有文件先 `inspect(view="layout", flowsheet_path=...)`，读 `graphics.nodes/edges/metadata`、原生 PNG 和 `inspection_path` 中的完整图形，再调用 `layout` 编辑。对象通过原生 `name` 或唯一 `tag` 引用。分区语义由 AI 根据实际工艺资料确定，自动布局负责拓扑排列。

```json
{
  "action": "layout",
  "flowsheet_path": "data/example.dwxml",
  "output_format": "dwxml",
  "layout": {
    "mode": "auto",
    "groups": [{"id": "heating", "title": "进料与加热", "members": ["HT-101"]}],
    "pinned": [],
    "nodes": []
  }
}
```

`groups` 成员可只列设备，物流归入上游设备分区，进料归入下游分区；剩余连通分量另建分区。分区可给 `x/y` 指定内容起点；手动模式移动成员，固定对象保留原位。省略分区配置时沿用嵌入文件的分区；新的非空列表替换现有分区定义。

局部精调：`layout={"mode":"manual","nodes":[{"object":"HT-101","x":500,"y":300,"rotation":90,"width":70,"height":100,"flipped_h":false}]}`。位置须同时给 x/y；尺寸为画布尺寸，旋转支持 0/90/180/270，另支持 `flipped_v`。自动模式中显式位置也作为固定约束。`pinned` 固定当前位置，省略时沿用保存的固定对象，显式 `[]` 清除旧约束；冲突返回重叠对象。默认间距 `grid=20, layer_gap=220, node_gap=100, group_gap=240`，单位为原生画布单位。

`layout` 不接受工艺 updates、物性或初始化。它保留原文件，将允许修改的图形字段、原生分区框和带中文字体 CSS 的原生 HTML 文字、`ChemELLM_layout_v1` 元数据写入新文件，再真实载入新文件检查。工艺及连接数据保护见 `protected_data_unchanged`，重开检查见 `saved_reload_verified`。布局不返回新的 `solved/validation` 结论；需要计算验收时再对保存后的文件执行 `run`。

`saved_flowsheet` 是新原生文件，`layout_path` 是完整前后图形与质量报告，`previews` 是 DWSIM 原生 Skia 渲染图。大分区按画布范围裁切为细节 PNG，总览用于定位。`layout_quality.before/after` 列出符号及标签包络重叠、连线交叉、穿过设备和长连接；`passed` 仅表示包络无重叠，不等于全部走线理想或工艺验收通过。查看预览后以手动模式继续精调，保留未解决问题。

连线由 DWSIM 原生重建，不提供无法稳定保存的任意折点。分区图形不增加计算设备或改变端口。默认输出格式：layout 沿用输入，create/run 为 dwxmz；用 `output_format` 显式选择 dwxml/dwxmz。DWXML 去除节点间排版空白以控制尺寸，数值和文本保留；超过 25 MiB 时明确报错，可显式选择 dwxmz。
