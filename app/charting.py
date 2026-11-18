"""Build reproducible Vega-Lite charts from recorded project data."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import Field, RootModel, model_validator

from app.datasets import dataset_source
from app.db import one
from app.dwsim_workflows import Metric, metric_value, resolve
from app.schemas import Inputs
from app.tabular import numeric_column, read_table

VEGA_LITE_VERSION = "5.21"
FORMATS = ("svg", "pdf", "png")
FORMAT = Literal["svg", "pdf", "png"]
PALETTE = ["#176589", "#ad541b", "#227555", "#833d75", "#5d55a3", "#77641a"]


class ChartOptions(Inputs):
    title: str = Field(min_length=1, max_length=160)
    formats: list[FORMAT] = Field(default_factory=lambda: list(FORMATS), min_length=1, max_length=3)

    @model_validator(mode="after")
    def check_formats(self):
        if not self.title.strip() or len(set(self.formats)) != len(self.formats):
            raise ValueError("标题须非空，输出格式不得重复")
        return self


class TableChart(ChartOptions):
    mode: Literal["table"]
    artifact_id: str = Field(min_length=1, max_length=64)
    sheet: str | None = Field(default=None, max_length=120)
    mark: Literal["point", "line", "bar"]
    x_column: str = Field(min_length=1, max_length=120)
    y_column: str = Field(min_length=1, max_length=120)
    x_unit: str = Field(min_length=1, max_length=60, description="分类轴填 dimensionless")
    y_unit: str = Field(min_length=1, max_length=60)
    group_column: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def distinct_columns(self):
        columns = [name for name in (self.x_column, self.y_column, self.group_column) if name is not None]
        if len(set(columns)) != len(columns):
            raise ValueError("横轴、纵轴和分组须选用不同列")
        return self


class ScanChart(ChartOptions):
    mode: Literal["dwsim_scan"]
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    metric_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")


class FitChart(ChartOptions):
    mode: Literal["fit"]
    calculation_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=80)


class ChartInput(RootModel[Annotated[TableChart | ScanChart | FitChart, Field(discriminator="mode")]]):
    pass


def numeric_axis(field, title, *, zero=False):
    return {"field": field, "type": "quantitative", "title": title,
            "scale": {"type": "linear", "zero": zero}}


def axis_title(name, unit):
    return name if unit == "dimensionless" else f"{name} [{unit}]"


def categories(table, column):
    from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula

    if column not in table["columns"]:
        raise ValueError("数据列不存在：" + column)
    index = table["columns"].index(column)
    values = []
    for row, number in zip(table["rows"], table["row_numbers"]):
        value = row[index]
        if (value is None or isinstance(value, (ArrayFormula, DataTableFormula))
                or not str(value).strip() or str(value).lstrip().startswith("=")):
            raise ValueError(f"第{number}行的 {column} 列须有分类值，且不能是公式")
        values.append(str(value).strip())
    return values


def table_chart(project_id, request):
    record, path, digest = dataset_source(project_id, request)
    table = read_table(path, request.sheet)
    categorical = request.mark == "bar"
    x_values = (categories if categorical else numeric_column)(table, request.x_column)
    y_values = numeric_column(table, request.y_column)
    groups = categories(table, request.group_column) if request.group_column else [None] * len(y_values)
    rows = [{"x": x, "y": y, "group": group, "source_row": row}
            for x, y, group, row in zip(x_values, y_values, groups, table["row_numbers"])]
    if categorical:
        seen = set()
        for row in rows:
            key = row["x"], row["group"]
            if key in seen:
                raise ValueError(f"第{row['source_row']}行重复了柱图类别和分组，请提供每组每类别的单一数值")
            seen.add(key)
    if request.mark == "line":
        rows.sort(key=lambda row: (row["group"] or "", row["x"], row["source_row"]))

    x_title = axis_title(request.x_column, request.x_unit)
    x_axis = {"field": "x", "type": "nominal", "title": x_title, "sort": None,
              "axis": {"labelAngle": -25}} if categorical else numeric_axis("x", x_title)
    encoding = {"x": x_axis, "y": numeric_axis("y", axis_title(request.y_column, request.y_unit), zero=categorical)}
    mark = {"type": request.mark, "color": PALETTE[0]}
    if request.group_column:
        encoding["color"] = {"field": "group", "type": "nominal", "title": request.group_column}
        if categorical:
            encoding["xOffset"] = {"field": "group", "type": "nominal"}
    if request.mark == "line":
        encoding["order"] = {"field": "x", "type": "quantitative"}
        mark["point"] = True
    if request.mark == "point":
        mark.update(filled=True, size=45, opacity=.85)
    body = {"width": 620, "height": 340, "data": {"name": "points"}, "mark": mark, "encoding": encoding}
    provenance = {"artifact_id": record["id"], "name": record["name"], "sha256": digest, "sheet": table["sheet"],
                  "columns": {"x": request.x_column, "y": request.y_column, "group": request.group_column},
                  "units": {"x": request.x_unit, "y": request.y_unit},
                  "excluded_rows": [{"source_row": row, "reason": "全空行"} for row in table["blank_rows"]]}
    return body, {"points": rows}, provenance, []


def calculation(project_id, calculation_id, allowed):
    record = one("SELECT tool,result FROM calculations WHERE id=? AND project_id=?", (calculation_id, project_id))
    if not record or record["tool"] not in allowed:
        raise ValueError("未找到当前项目中所需类型的计算记录")
    result = json.loads(record["result"])
    digest = hashlib.sha256(record["result"].encode("utf-8")).hexdigest()
    return result, {"calculation_id": calculation_id, "tool": record["tool"], "result_sha256": digest}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


SCAN_AXES = {"temperature_k": ("扫描温度", "K"), "pressure_pa": ("扫描压力", "Pa"),
            "mass_flow_kg_s": ("扫描质量流量", "kg/s"), "reflux_ratio": ("扫描回流比", "dimensionless")}
SCAN_STATUS = {"valid": "通过", "unsolved": "未求解", "invalid": "校核未通过",
               "missing": "指标或结果缺失", "cancelled": "未执行"}


def scan_point(project_id, case, index, x, metric):
    row = {"scan_index": index, "x": x, "y": None, "case": case["key"], "case_status": case["status"],
           "calculation_id": case.get("calculation_id"), "result_sha256": None, "result_path": None,
           "denominator_result_path": None, "solved": None, "validation_passed": None}
    status = "cancelled" if case["status"] in {"pending", "cancelled"} else "missing"
    reason = "工况没有保存计算结果"
    if row["calculation_id"]:
        try:
            result, source = calculation(project_id, row["calculation_id"], {"dwsim"})
            row.update(result_sha256=source["result_sha256"], solved=result.get("solved"),
                       validation_passed=(result.get("validation") or {}).get("passed"))
            if row["solved"] is not True:
                status, reason = "unsolved", "求解未成功或状态缺失"
            elif row["validation_passed"] is not True:
                status, reason = "invalid", "校核未通过或状态缺失"
            else:
                value, pointer = metric_value(result, metric)
                row["result_path"] = pointer
                if metric.denominator_path:
                    row["denominator_result_path"] = resolve(result, metric.denominator_path)[1]
                if not finite(value):
                    raise ValueError("指标不是有限数值")
                row["y"] = value
                status, reason = "valid", None
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            status, reason = "missing", str(exc)
    return {**row, "status": SCAN_STATUS[status], "excluded_reason": reason}


def scan_chart(project_id, request, study_path):
    try:
        content = study_path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError("未找到本项目的 DWSIM 扫描任务") from exc
    study = json.loads(content)
    if study.get("project_id") != project_id or study.get("job_id") != request.job_id:
        raise ValueError("DWSIM 扫描任务标识不一致")
    if study.get("status") not in {"completed", "completed_with_failures", "cancelled"}:
        raise ValueError("请选择已结束的 DWSIM 扫描任务")
    definition = study["request"]
    scan = definition.get("scan")
    if not scan or scan["field"] not in SCAN_AXES:
        raise ValueError("此任务没有可用的数值扫描轴")
    raw_metric = next((item for item in definition["metrics"] if item["key"] == request.metric_key), None)
    if not raw_metric:
        raise ValueError("扫描任务没有此指标：" + request.metric_key)
    metric = Metric.model_validate(raw_metric)
    values, cases = scan["values"], {case["key"]: case for case in study["cases"]}
    if not values or len(values) != len(cases) or set(cases) != {f"point-{i}" for i in range(1, len(values)+1)}:
        raise ValueError("扫描值与保存的工况无法一一对应")
    rows = sorted((scan_point(project_id, cases[f"point-{i}"], i, x, metric)
                   for i, x in enumerate(values, 1)), key=lambda row: (row["x"], row["scan_index"]))
    segment = 0
    for row in rows:
        if row["excluded_reason"] is not None:
            segment += 1
            row["segment"] = None
        else:
            row["segment"] = segment
    valid = [row for row in rows if row["segment"] is not None]
    x_name, x_unit = SCAN_AXES[scan["field"]]
    x = numeric_axis("x", axis_title(x_name, x_unit))
    y = numeric_axis("y", axis_title(metric.key, metric.unit))
    main = {"width": 620, "height": 285, "data": {"name": "valid"},
            "encoding": {"x": {**x, "axis": None}, "y": y},
            "layer": [{"mark": {"type": "line", "color": PALETTE[0], "strokeWidth": 2},
                       "encoding": {"detail": {"field": "segment", "type": "nominal"},
                                    "order": {"field": "x", "type": "quantitative"}}},
                      {"mark": {"type": "point", "filled": True, "color": PALETTE[0], "size": 52}}]}
    states = list(SCAN_STATUS.values())
    used = [state for state in states if any(row["status"] == state for row in rows)]
    status_colors = dict(zip(states, ["#227555", "#b13936", "#9d4f24", "#875298", "#6d7681"]))
    strip = {"width": 620, "height": 18, "data": {"name": "cases"},
             "mark": {"type": "point", "size": 75, "filled": True}, "encoding": {"x": x,
                "color": {"field": "status", "type": "nominal", "title": "工况状态",
                          "scale": {"domain": used, "range": [status_colors[state] for state in used]}}}}
    provenance = {"job_id": request.job_id, "study_sha256": hashlib.sha256(content).hexdigest(),
                  "baseline_sha256": study.get("baseline_sha256"), "scan": {"tag": scan["tag"], "field": scan["field"]},
                  "metric": metric.model_dump(), "units": {"x": x_unit, "y": metric.unit},
                  "exclusions": [{"case": row["case"], "scan_index": row["scan_index"], "reason": row["excluded_reason"]}
                                 for row in rows if row["excluded_reason"] is not None]}
    notes = [f"{scan['tag']} · {len(valid)} / {len(rows)} 个工况进入曲线"]
    if not valid:
        notes.append("没有同时通过求解和校核且具备指标的工况")
    return {"vconcat": [main, strip], "spacing": 6, "resolve": {"scale": {"x": "shared"}}}, \
        {"cases": rows, "valid": valid}, provenance, notes


def fit_chart(project_id, request):
    result, provenance = calculation(project_id, request.calculation_id, {"analyze_dataset", "fit_data"})
    fits = result.get("fits", []) if provenance["tool"] == "analyze_dataset" else [result]
    if request.model:
        fits = [(i, fit) for i, fit in enumerate(fits) if fit.get("model") == request.model]
    else:
        fits = list(enumerate(fits))
    if len(fits) != 1:
        raise ValueError("请选择一个已保存的拟合模型")
    index, fit = fits[0]
    if (fit.get("validation") or {}).get("numerical_fit_converged") is not True:
        raise ValueError("拟合结果没有已收敛的数值记录")
    prefix = f"/fits/{index}" if provenance["tool"] == "analyze_dataset" else ""
    source = result.get("source", {})
    source_rows = source.get("source_row_numbers", [])
    observations = [{"x": row["x"], "y": row["y"], "result_path": f"{prefix}/observations/{i}",
                     "source_row": source_rows[i] if i < len(source_rows) else None}
                    for i, row in enumerate(fit["observations"])]
    curve = [{"x": row["x"], "y": row["y"], "bounds": row.get("mean_confidence_interval"),
              "extrapolated": row.get("extrapolated", False), "result_path": f"{prefix}/curve/{i}"}
             for i, row in enumerate(fit["curve"])]
    if not observations or not curve or not all(finite(row["x"]) and finite(row["y"]) for row in observations + curve):
        raise ValueError("拟合记录没有可用的有限数值观测或曲线")
    curve.sort(key=lambda row: row["x"])
    # Duplicate shared endpoints when the stroke switches to extrapolation.
    lines = []
    previous_style = None
    segment = 0
    for left, right in zip(curve, curve[1:]):
        style = "外推" if left["extrapolated"] or right["extrapolated"] else "拟合"
        if style != previous_style:
            segment += 1
            lines.append({**left, "style": style, "segment": segment})
        lines.append({**right, "style": style, "segment": segment})
        previous_style = style
    if not lines:
        lines = [{**curve[0], "style": "外推" if curve[0]["extrapolated"] else "拟合", "segment": 0}]
    bands = []
    band_segment = 0
    for row in curve:
        interval = row["bounds"]
        if interval is None:
            band_segment += 1
        elif (not isinstance(interval, list) or len(interval) != 2 or not all(map(finite, interval))
              or not interval[0] <= interval[1]):
            raise ValueError("拟合结果包含无效的均值置信区间：" + row["result_path"])
        else:
            bands.append({**row, "lower": interval[0], "upper": interval[1], "segment": band_segment})

    units = {"x": fit["x_unit"], "y": fit["y_unit"]}
    x = numeric_axis("x", axis_title(source.get("x_column") or "x", units["x"]))
    y = numeric_axis("y", axis_title(source.get("y_column") or "y", units["y"]))
    layers = []
    notes = [f"模型 {fit['model']} · 实心点为观测值"]
    if bands:
        confidence = fit.get("confidence")
        if not finite(confidence) or not 0 < confidence < 1:
            raise ValueError("已存置信区间缺少有效置信水平")
        notes.append(f"{confidence:.3%}".rstrip("%").rstrip("0").rstrip(".") + "% 局部模型均值置信带")
        if len(bands) != len(curve):
            notes.append("部分拟合点缺少置信区间")
        layers.append({"data": {"name": "bands"}, "mark": {"type": "area", "color": PALETTE[0], "opacity": .19},
                       "encoding": {"x": x, "y": {**y, "field": "lower"}, "y2": {"field": "upper"},
                                    "detail": {"field": "segment", "type": "nominal"}}})
    else:
        notes.append("拟合记录未提供均值置信区间")
    if any(row["extrapolated"] for row in curve):
        notes.append("虚线为外推")
    layers += [{"data": {"name": "lines"}, "mark": {"type": "line", "strokeWidth": 2.3, "color": PALETTE[0]},
                "encoding": {"x": x, "y": y, "detail": {"field": "segment", "type": "nominal"},
                             "strokeDash": {"field": "style", "type": "nominal", "legend": None,
                                            "scale": {"domain": ["拟合", "外推"], "range": [[1, 0], [5, 4]]}}}},
               {"data": {"name": "observations"}, "mark": {"type": "point", "size": 56, "filled": True,
                                                           "color": "#193240", "opacity": .85},
                "encoding": {"x": x, "y": y}}]
    provenance.update(model=fit["model"], result_path=prefix, units=units, dataset=source or None,
                      interval={"type": "local_mean_confidence", "confidence": fit.get("confidence"),
                                "method": fit.get("uncertainty_method"), "points": len(bands)})
    return {"width": 620, "height": 340, "layer": layers}, \
        {"observations": observations, "curve": curve, "lines": lines, "bands": bands}, provenance, notes


def make_spec(title, body, data, notes, font):
    return {"$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "datasets": data, "title": {"text": title, "subtitle": notes, "anchor": "start", "offset": 18},
            "padding": 16, "background": "white", "config": {
                "font": font, "view": {"stroke": None}, "range": {"category": PALETTE},
                "axis": {"domainColor": "#8f9ca2", "tickColor": "#8f9ca2", "gridColor": "#e5eaed",
                         "labelColor": "#293d48", "titleColor": "#293d48", "labelFontSize": 12,
                         "titleFontSize": 13, "titlePadding": 9},
                "title": {"fontSize": 20, "subtitleFontSize": 12, "subtitleColor": "#475c68",
                          "subtitlePadding": 6, "color": "#172e3a"},
                "legend": {"orient": "bottom", "labelFontSize": 11, "titleFontSize": 12}},
            **body}
