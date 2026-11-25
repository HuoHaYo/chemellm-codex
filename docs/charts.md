# Vega-Lite 科研图表

先用 `chem_tools(tool="export_chart")` 查看参数，再通过 `chem_execute` 调用。AI 可根据研究问题选择图型、列、分组和标题，插件从项目已有记录取数，并用本机渲染器生成 SVG、单页 PDF 和 PNG。`formats` 可指定所需格式。PNG 同时用作预览。

## 表格图

通过 `chem_intake` 将 CSV 或 XLSX 登记到项目，再用 `dataset_inspect` 查看列名和工作表。以下内容可作为 `chem_execute` 的 `args`。

```json
{
  "mode": "table",
  "title": "温度与吸附量",
  "artifact_id": "替换为项目文件 ID",
  "sheet": "实验",
  "mark": "line",
  "x_column": "温度",
  "x_unit": "°C",
  "y_column": "吸附量",
  "y_unit": "μmol·m⁻²",
  "group_column": "样品"
}
```

CSV 省略 `sheet`。散点用 `point`，折线用 `line`，分类柱图用 `bar`。柱图从零开始，分类轴单位填 `dimensionless`，每个类别和分组对应一个输入数值。横纵轴单位按原数据填写。表格模式直接绘制原数值。折线按组内横轴数值排序，所选字段无效时返回源行和列名。

## 仿真扫描与实验拟合

DWSIM 扫描结束后，用所保存的任务 ID 与指标 key 出图。图的横轴为数值扫描轴，纵轴采用指标登记单位。曲线在求解、校核或取值失败处断开，下面的状态条对应同一横轴。

```json
{
  "mode": "dwsim_scan",
  "title": "进料温度对加热负荷的影响",
  "job_id": "替换为 32 位十六进制任务 ID",
  "metric_key": "duty"
}
```

拟合图直接采用 `fit_data` 或 `analyze_dataset` 的计算记录。包含多个拟合时用 `model` 选定其中一个。实心点为观测，虚线为外推，置信带采用保存的局部模型均值区间，并在副标题标明置信水平与缺口。

```json
{
  "mode": "fit",
  "title": "实验观测与线性回归",
  "calculation_id": "替换为项目计算 ID",
  "model": "linear"
}
```

## 归档与离线重绘

选用任一图表打包后，ZIP 的 `charts/<图表ID>/` 内带有三份重绘资料。

| 文件 | 内容 |
| --- | --- |
| `chart.vl.json` | Vega-Lite 规格，包含全部内联数据 |
| `chart-data.json` | 实际入图数据，附源行号、结果位置或工况状态 |
| `chart-source.json` | 原始来源及哈希、轴单位、排除理由、渲染版本、字体和成果哈希 |

使用 Windows 锁文件中的 `vl-convert-python==1.9.0.post1`，新图默认 Vega-Lite 6.4；需要旧规格时传 `"vega_lite_version": "5.21"`。离线重绘以归档清单中的版本为准。中文优先使用微软雅黑，缺字时按本机可用字体回退。PDF 成果包含实际字体子集，SVG 的可编辑文字随查看器选用本机字体。

在解包后的图表目录运行以下 Python 代码，即可重绘三种格式。

```python
import json
from pathlib import Path
import vl_convert as vl

spec = json.loads(Path("chart.vl.json").read_text(encoding="utf-8"))
manifest = json.loads(Path("chart-source.json").read_text(encoding="utf-8"))
options = {"vl_version": manifest["renderer"]["vega_lite"], "allowed_base_urls": []}
Path("replay.svg").write_text(vl.vegalite_to_svg(spec, **options), encoding="utf-8")
Path("replay.pdf").write_bytes(vl.vegalite_to_pdf(spec, **options))
Path("replay.png").write_bytes(vl.vegalite_to_png(spec, **options))
```
