"""Project-owned experimental datasets, analysis outputs and provenance."""
import csv
import hashlib
import json
import threading
from pathlib import Path

from pydantic import Field, model_validator

from app import config
from app.db import one, uid
from app.documents import artifact_record
from app.experimental import Model, MODELS
from app.schemas import Inputs
from app.tabular import read_table, numeric_column

_plot_lock = threading.Lock()


class DatasetInspectInput(Inputs):
    artifact_id: str = Field(min_length=1, max_length=64)
    sheet: str | None = Field(default=None, max_length=120)


class DatasetAnalysisInput(DatasetInspectInput):
    x_column: str = Field(min_length=1, max_length=120)
    y_column: str = Field(min_length=1, max_length=120)
    x_unit: str = Field(default="dimensionless", min_length=1, max_length=60)
    y_unit: str = Field(default="dimensionless", min_length=1, max_length=60)
    models: list[Model] = Field(default=["linear", "first_order_uptake"], min_length=1, max_length=5)
    sigma_column: str | None = Field(default=None, max_length=120)
    absolute_sigma: bool = False
    confidence: float = Field(default=0.95, ge=0.5, le=0.999)

    @model_validator(mode="after")
    def distinct(self):
        if self.x_column == self.y_column or len(set(self.models)) != len(self.models):
            raise ValueError("自变量与因变量须为不同列，候选模型不能重复")
        if self.absolute_sigma and not self.sigma_column:
            raise ValueError("已知绝对误差模式须选择标准差列")
        return self


def dataset_source(project_id, p):
    row = one("SELECT * FROM artifacts WHERE id=? AND project_id=?", (p.artifact_id, project_id))
    if not row:
        raise ValueError("数据文件必须属于当前项目")
    path = Path(row["path"]).resolve()
    if not path.is_relative_to(config.ARTIFACTS) or not path.is_file():
        raise ValueError("数据文件路径无效或文件缺失")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != json.loads(row["metadata"]).get("sha256"):
        raise ValueError("数据文件哈希发生变化；请重新上传以保留输入版本")
    return row, path, digest


def inspect_dataset(project_id, p):
    row, path, digest = dataset_source(project_id, p)
    table = read_table(path, p.sheet)
    # Dates and other Excel scalar types are displayed as strings, never coerced to numbers.
    preview = [[str(v) if v is not None else "" for v in r] for r in table["rows"][:12]]
    return {"artifact_id": row["id"], "name": row["name"], "sha256": digest,
            "columns": table["columns"], "rows": len(table["rows"]), "sheet": table["sheet"],
            "sheets": table["sheets"], "preview": preview, "blank_rows": table["blank_rows"],
            "policy": "首行为列名；仅跳过并记录全空行；所选数值列的缺失/公式/非数值将拒绝分析。"}


def _plot(path, results):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    with _plot_lock:
        fig = Figure(figsize=(8, 6), constrained_layout=True)
        FigureCanvasAgg(fig)
        axes = fig.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [2, 1]})
        first = results[0]
        axes[0].scatter([r["x"] for r in first["observations"]], [r["y"] for r in first["observations"]], color="#182f2b", s=25, label="observations", zorder=3)
        for result in results:
            curve = sorted(result["curve"], key=lambda row: row["x"])
            line, = axes[0].plot([r["x"] for r in curve], [r["y"] for r in curve], label=result["model"])
            if all(r["mean_confidence_interval"] is not None for r in curve):
                axes[0].fill_between([r["x"] for r in curve], [r["mean_confidence_interval"][0] for r in curve], [r["mean_confidence_interval"][1] for r in curve], alpha=.12, color=line.get_color())
            axes[1].scatter([r["x"] for r in result["observations"]], [r["residual"] for r in result["observations"]], label=result["model"], s=20)
        axes[0].set_ylabel("y ["+first["y_unit"]+"]")
        axes[1].set_xlabel("x ["+first["x_unit"]+"]")
        axes[1].set_ylabel("observed - fitted")
        axes[1].axhline(0, color="gray", linewidth=.8)
        axes[0].legend(fontsize=8)
        axes[0].set_title("Regression comparison (local mean confidence bands)")
        for axis in axes:
            axis.grid(alpha=.2)
        fig.savefig(path.with_suffix(".svg"))
        fig.savefig(path.with_suffix(".pdf"))
        fig.clear()


def analyze_dataset(project_id, p):
    from app.toolbox import run_tool
    row, path, digest = dataset_source(project_id, p)
    table = read_table(path, p.sheet)
    x = numeric_column(table, p.x_column)
    y = numeric_column(table, p.y_column)
    sigma = numeric_column(table, p.sigma_column) if p.sigma_column else None
    results, failures = [], []
    for model in p.models:
        args = {"x": x, "y": y, "sigma_y": sigma, "absolute_sigma": p.absolute_sigma,
                "model": model, "x_unit": p.x_unit, "y_unit": p.y_unit, "confidence": p.confidence}
        try:
            results.append(run_tool(project_id, "fit_data", args))
        except (ValueError, FloatingPointError, OverflowError) as exc:
            failures.append({"model": model, "error": str(exc)})
    if not results:
        raise ValueError("所有候选模型均失败："+json.dumps(failures, ensure_ascii=False))
    criterion = "AICc" if all(r["metrics"]["AICc"] is not None for r in results) else "AIC"
    comparable = [r for r in results if r["metrics"][criterion] is not None]
    ranking = sorted([{"model": r["model"], "criterion_value": r["metrics"][criterion]} for r in comparable], key=lambda r: r["criterion_value"])
    for r in ranking:
        r["delta"] = r["criterion_value"]-ranking[0]["criterion_value"]
    provenance = {"artifact_id": row["id"], "name": row["name"], "sha256": digest, "sheet": table["sheet"],
                  "x_column": p.x_column, "y_column": p.y_column, "sigma_column": p.sigma_column,
                  "source_row_numbers": table["row_numbers"], "blank_rows": table["blank_rows"]}
    output = {"source": provenance, "fits": results, "failures": failures,
              "comparison": {"criterion": criterion, "ranking": ranking,
                             "interpretation": "同一数据及误差假设下的信息准则比较；小值优先，不构成机理验证或新颖性证明。"},
              "status": "research_analysis_requires_review",
              "warnings": ["观测数值与原文件哈希关联；不确认数据的实验真实性。", "区间是模型均值的局部置信区间，不是新观测预测区间。"]}
    folder = config.ARTIFACTS/uid()
    folder.mkdir()
    report = folder/"analysis.json"
    report.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    plot = folder/"regression"
    _plot(plot, results)
    residual_file = folder/"residuals.csv"
    with residual_file.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["model", "source_row", "x", "observed_y", "fitted_y", "residual", "standardized_residual"])
        for result in results:
            for number, observation in zip(table["row_numbers"], result["observations"]):
                writer.writerow([result["model"], number, *[observation[k] for k in ["x", "y", "predicted", "residual", "standardized_residual"]]])
    meta = {"status": "research_analysis_requires_review", "source": provenance}
    output["artifacts"] = [artifact_record(project_id, target, text=report.read_text(encoding="utf-8") if target == report else "", metadata=meta)
                           for target in [report, residual_file, plot.with_suffix(".svg"), plot.with_suffix(".pdf")]]
    return output
