"""Independent NIST certified regression references, not model-generated truth."""
import csv
import hashlib
import io
import json
import re

from app import config

CASES = {
    "Misra1a": {"model": "first_order_uptake", "x_name": "pressure_source_units", "y_name": "volume_source_units", "x_unit": "dimensionless", "y_unit": "dimensionless", "topic": "单分子吸附：源文件未声明数值单位，基准沿用原始数值尺度"},
    "BoxBOD": {"model": "first_order_uptake", "x_name": "time_day", "y_name": "BOD_mg_liter", "x_unit": "day", "y_unit": "mg/liter", "topic": "生化耗氧量与培养时间"},
    "DanWood": {"model": "power_law", "x_name": "temperature_in_1000_K", "y_name": "radiated_energy_source_units", "x_unit": "dimensionless", "y_unit": "dimensionless", "topic": "碳丝灯热辐射：x为以1000 K计的温度，y沿用源文件数值尺度"},
}
TOLERANCES = {"parameter_relative": 2e-6, "standard_error_relative": 2e-5, "SSE_relative": 1e-7}
ROOT = config.ROOT/"docs"/"sources"/"nist-strd"
FLOAT = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[Ee][+-]?\d+)?"


def reference_case(name):
    if name not in CASES:
        raise ValueError("未知的公开数据案例")
    path = ROOT/(name+".dat")
    expected = json.loads((ROOT/"SHA256SUMS.json").read_text(encoding="utf-8"))[path.name]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError("NIST 原始参考文件哈希不一致")
    text = path.read_text(encoding="ascii")
    parameter_rows = re.findall(r"^\s*b\d+\s*=\s*("+FLOAT+r")\s+("+FLOAT+r")\s+("+FLOAT+r")\s+("+FLOAT+r")\s*$", text, re.M)
    if len(parameter_rows) != 2:
        raise ValueError("NIST 认证参数段无法解析")
    data = re.split(r"^Data:\s+y\b.*$", text, flags=re.M)
    if len(data) != 2:
        raise ValueError("NIST 数据段无法解析")
    observed = re.findall(r"^\s*("+FLOAT+r")\s+("+FLOAT+r")\s*$", data[1], re.M)
    sse = re.search(r"Residual Sum of Squares:\s*("+FLOAT+")", text)
    if not observed or not sse:
        raise ValueError("NIST 参考不完整")
    return {**CASES[name], "name": name, "x": [float(row[1]) for row in observed], "y": [float(row[0]) for row in observed],
            "certified_parameters": [float(row[2]) for row in parameter_rows], "certified_standard_errors": [float(row[3]) for row in parameter_rows],
            "certified_SSE": float(sse.group(1)), "sha256": digest,
            "url": "https://www.itl.nist.gov/div898/strd/nls/data/LINKS/DATA/"+path.name}


def example_csv(name):
    reference = reference_case(name)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([reference["x_name"], reference["y_name"]])
    writer.writerows(zip(reference["x"], reference["y"]))
    return buffer.getvalue().encode("utf-8"), reference


def evaluate_regression():
    from app.experimental import FitInput, fit_data
    import platform
    import numpy
    import scipy
    results = []
    for name in CASES:
        reference = reference_case(name)
        try:
            actual = fit_data(FitInput(x=reference["x"], y=reference["y"], model=reference["model"], x_unit=reference["x_unit"], y_unit=reference["y_unit"]))
            comparisons = {}
            for key, actual_values, expected_values, tolerance in [
                ("parameters", [v["value"] for v in actual["parameters"]], reference["certified_parameters"], TOLERANCES["parameter_relative"]),
                ("standard_errors", [v["standard_error"] for v in actual["parameters"]], reference["certified_standard_errors"], TOLERANCES["standard_error_relative"]),
                ("SSE", [actual["metrics"]["SSE"]], [reference["certified_SSE"]], TOLERANCES["SSE_relative"]),
            ]:
                errors = [abs(a-b)/abs(b) if a is not None else None for a, b in zip(actual_values, expected_values)]
                comparisons[key] = {"actual": actual_values, "certified": expected_values, "relative_errors": errors,
                                    "relative_tolerance": tolerance, "passed": all(e is not None and e <= tolerance for e in errors)}
            results.append({"name": name, "topic": reference["topic"], "source_url": reference["url"], "source_sha256": reference["sha256"],
                            "observations": len(reference["x"]), "comparisons": comparisons, "passed": all(r["passed"] for r in comparisons.values())})
        except (ValueError, FloatingPointError, OverflowError) as exc:
            results.append({"name": name, "passed": False, "error": str(exc)})
    return {"scope": "certified_numerical_regression_qualification", "passed": all(r["passed"] for r in results), "results": results,
            "environment": {"python": platform.python_version(), "numpy": numpy.__version__, "scipy": scipy.__version__},
            "limitations": ["三个公开案例用于数值资格验证，参考值在开发期间可见，不作为盲测或保留测试集。", "不验证化学机理、未知体系外推、LLM工具选择或全厂工程模型。", "独立物性/流程基准和LLM保留评测仍待建设。"]}
