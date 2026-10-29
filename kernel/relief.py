"""Safety valve / rupture disc sizing calculation.

Implements compressible (gas/vapour) critical and subcritical flow from first
principles (ideal gas), the API 520 liquid sizing relationship, and the API 521
fire case, then selects an API 526 standard orifice and checks capacity,
backpressure and set pressure.

API 520/521/526 originals are not in the workspace. Every formula is labelled
with its public source and a "待标准原文核对" note; the tool never claims
code compliance. Outputs DOCX, PDF and JSON calculation sheets.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ORIFICES_IN2 = [
    ("D", 0.110), ("E", 0.196), ("F", 0.307), ("G", 0.503), ("H", 0.785),
    ("J", 1.287), ("K", 1.838), ("L", 2.853), ("M", 3.60), ("N", 4.34),
    ("P", 6.38), ("Q", 11.05), ("R", 16.0), ("T", 26.0),
]
MM2_PER_IN2 = 645.16
ATM_MPA = 0.101325
R_UNIVERSAL = 8314.462618  # J/(kmol*K)

SOURCES = [
    "API 520 Part I（泄放量/口径计算，公开资料；原文未取得，待核对）",
    "API 520 Part II（安装/背压，公开资料；原文未取得，待核对）",
    "API 521（火灾工况，公开资料；原文未取得，待核对）",
    "API 526（标准孔径，公开资料；原文未取得，待核对）",
    "GB/T 150.1-2024（设计压力/温度适用范围，本项目已登记）",
    "HG/T 20570.2-95（安全阀设置和选用，扫描页已渲染；适用范围 >0.2 MPa 压力容器）",
]
LIMITS = [
    "本计算为方案筛选，不替代厂家选型与 API/GB 正式校核。",
    "气体按理想气体、绝热指数与压缩因子由用户给定；未计两相流、湿蒸汽、聚合/结焦与振动。",
    "火灾工况采用 API 521 液体润湿面积公式；未计保温、泄放管阻力与火炬背压。",
    "标准孔径为 API 526 常用值；实际以厂家样本与铭牌为准。",
    "API 原文未取得，公式与限值需按正式标准/项目规定复核。",
]


def _num(value, default=None):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _validate(payload: dict) -> list[str]:
    errors: list[str] = []
    if str(payload.get("scenario") or "") not in {"blocked_outlet", "fire"}:
        errors.append("scenario 必须是 blocked_outlet 或 fire")
    if str(payload.get("fluid") or "gas") not in {"gas", "vapor", "liquid"}:
        errors.append("fluid 必须是 gas/vapor/liquid")
    fluid = str(payload.get("fluid") or "gas")
    if fluid in {"gas", "vapor"}:
        mw = _num(payload.get("molecular_weight"))
        if not mw or mw <= 0:
            errors.append("气体工况必须给出 molecular_weight (kg/kmol)")
        k = _num(payload.get("k"))
        if not k or k <= 1.0 or k > 2.0:
            errors.append("绝热指数 k 必须在 1~2 之间")
        temperature = _num(payload.get("temperature_K"))
        if not temperature or temperature <= 0:
            errors.append("必须给出 temperature_K")
    set_pressure = _num(payload.get("set_pressure_mpag"))
    if set_pressure is None or set_pressure <= 0:
        errors.append("set_pressure_mpag 必须 >0")
    design_pressure = _num(payload.get("design_pressure_mpag"))
    if design_pressure is None or design_pressure <= 0:
        errors.append("design_pressure_mpag 必须 >0")
    backpressure = _num(payload.get("backpressure_mpag"), 0.0) or 0.0
    if backpressure < 0:
        errors.append("backpressure_mpag 不能为负")
    if set_pressure is not None and backpressure is not None and backpressure >= set_pressure:
        errors.append("背压必须小于设定压力")
    if set_pressure is not None and design_pressure is not None and set_pressure > design_pressure:
        errors.append("设定压力不能高于设计压力")
    if str(payload.get("scenario")) == "blocked_outlet":
        if fluid in {"gas", "vapor"}:
            flow = _num(payload.get("relief_flow_kg_h"))
            if not flow or flow <= 0:
                errors.append("blocked_outlet 气体工况必须给出 relief_flow_kg_h")
        else:
            flow = _num(payload.get("liquid_flow_m3_h"))
            if not flow or flow <= 0:
                errors.append("blocked_outlet 液体工况必须给出 liquid_flow_m3_h")
            density = _num(payload.get("liquid_density_kg_m3"))
            if not density or density <= 0:
                errors.append("液体工况必须给出 liquid_density_kg_m3")
    if str(payload.get("scenario")) == "fire":
        if fluid == "liquid":
            errors.append("液体火灾工况暂不支持（需两相/潜热模型），请用气体/蒸汽工况或单独计算")
        area = _num(payload.get("wetted_area_m2"))
        latent = _num(payload.get("latent_heat_kj_kg"))
        if not area or area <= 0:
            errors.append("fire 工况必须给出 wetted_area_m2")
        if not latent or latent <= 0:
            errors.append("fire 工况必须给出 latent_heat_kj_kg")
    kd = _num(payload.get("discharge_coefficient"))
    if kd is not None and not (0.4 <= kd <= 1.0):
        errors.append("discharge_coefficient 应在 0.4~1.0")
    return errors


def _orifice_mm2(letter: str) -> float | None:
    for name, area in ORIFICES_IN2:
        if name == letter.upper():
            return area * MM2_PER_IN2
    return None


def compute(payload: dict) -> dict:
    errors = _validate(payload)
    if errors:
        raise ValueError("安全阀/爆破片参数错误：" + "；".join(errors[:8]))
    device = str(payload.get("device_type") or "psv").lower()
    if device not in {"psv", "rupture_disc"}:
        raise ValueError("device_type 必须是 psv 或 rupture_disc")
    scenario = str(payload.get("scenario"))
    fluid = str(payload.get("fluid") or "gas")
    set_pressure = _num(payload.get("set_pressure_mpag"))
    design_pressure = _num(payload.get("design_pressure_mpag"))
    backpressure = _num(payload.get("backpressure_mpag"), 0.0) or 0.0
    overpressure = _num(payload.get("overpressure_fraction"), 0.10 if scenario != "fire" else 0.21) or 0.0
    valve_type = str(payload.get("valve_type") or "conventional").lower()
    kd_default = 0.975 if device == "psv" else 0.62
    kd = _num(payload.get("discharge_coefficient"), kd_default) or kd_default
    kb = _num(payload.get("backpressure_correction"), 1.0) or 1.0
    kc = _num(payload.get("combination_factor"), 1.0) or 1.0
    temperature = _num(payload.get("temperature_K"), 300.0)
    mw = _num(payload.get("molecular_weight"), 28.0)
    k = _num(payload.get("k"), 1.2)
    z = _num(payload.get("compressibility"), 1.0) or 1.0
    p1 = (set_pressure + overpressure * set_pressure + ATM_MPA)  # MPa absolute
    p2 = backpressure + ATM_MPA

    warnings: list[str] = []
    steps: list[dict] = []
    checks: list[dict] = []
    result = {"device_type": device, "scenario": scenario, "fluid": fluid,
              "set_pressure_mpag": set_pressure, "design_pressure_mpag": design_pressure,
              "backpressure_mpag": backpressure, "overpressure_fraction": overpressure,
              "p1_mpaa": round(p1, 4), "p2_mpaa": round(p2, 4), "kd": kd, "kb": kb, "kc": kc}

    if fluid in {"gas", "vapor"}:
        result.update({"temperature_K": temperature, "molecular_weight": mw, "k": k,
                       "compressibility": z})
        critical_ratio = (2.0 / (k + 1.0)) ** (k / (k - 1.0))
        regime = "critical" if (p2 / p1) <= critical_ratio else "subcritical"
        rgas = R_UNIVERSAL / mw
        if scenario == "fire":
            wetted = _num(payload.get("wetted_area_m2"))
            latent = _num(payload.get("latent_heat_kj_kg"))
            env = _num(payload.get("environment_factor"), 1.0) or 1.0
            # API 521: Q = 21000 * F * A_ft2^0.82 Btu/h
            q_btu_h = 21000.0 * env * (wetted * 10.7639) ** 0.82
            q_w = q_btu_h * 0.293071
            w_kg_h = q_w / (latent * 1000.0) * 3600.0
            steps.append({"step": "火灾吸热", "formula": "Q=21000·F·A^0.82 Btu/h；W=Q/潜热",
                          "value": f"{w_kg_h:.1f} kg/h", "source": "API 521（公开式，待核对）"})
        else:
            w_kg_h = _num(payload.get("relief_flow_kg_h"))
        w_kg_s = w_kg_h / 3600.0
        if regime == "critical":
            mass_flux = p1 * 1e6 * math.sqrt(k / (rgas * temperature * z)) * (2.0 / (k + 1.0)) ** ((k + 1.0) / (2.0 * (k - 1.0)))
        else:
            ratio = p2 / p1
            mass_flux = p1 * 1e6 * math.sqrt(
                2.0 * k / ((k - 1.0) * rgas * temperature * z)
                * (ratio ** (2.0 / k) - ratio ** ((k + 1.0) / k)))
        area_mm2 = w_kg_s / (kd * kb * kc * mass_flux) * 1e6
        result.update({"regime": regime, "critical_ratio": round(critical_ratio, 4),
                       "mass_flux_kg_m2s": round(mass_flux, 3),
                       "relief_flow_kg_h": round(w_kg_h, 1), "required_area_mm2": round(area_mm2, 1)})
        steps.append({"step": "临界/亚临界判定", "formula": f"P2/P1={p2/p1:.4f}，临界比={critical_ratio:.4f}",
                      "value": regime, "source": "API 520（公开式，待核对）"})
        steps.append({"step": "质量通量", "formula": "G=P1·sqrt(k/(R·T·Z))·(2/(k+1))^((k+1)/(2(k-1)))（临界）",
                      "value": f"{mass_flux:.2f} kg/(m2·s)", "source": "API 520（公开式，待核对）"})
        steps.append({"step": "所需面积", "formula": "A=W/(Kd·Kb·Kc·G)", "value": f"{area_mm2:.1f} mm2",
                      "source": "API 520（公开式，待核对）"})
    else:
        flow = _num(payload.get("liquid_flow_m3_h"))
        density = _num(payload.get("liquid_density_kg_m3"))
        sg = density / 1000.0
        dp_kpa = (set_pressure + overpressure * set_pressure - backpressure) * 1000.0
        area_mm2 = 196.3 * math.sqrt(sg) * flow / (kd * math.sqrt(max(dp_kpa, 1e-6)))
        result.update({"regime": "liquid", "sg": round(sg, 3), "delta_p_kpa": round(dp_kpa, 1),
                       "relief_flow_m3_h": flow, "required_area_mm2": round(area_mm2, 1),
                       "liquid_flow_m3_h": flow, "liquid_density_kg_m3": density})
        steps.append({"step": "液体所需面积", "formula": "A=196.3·sqrt(SG)·Q/(Kd·sqrt(ΔP/kPa))",
                      "value": f"{area_mm2:.1f} mm2", "source": "API 520 液体式（公开式，待核对）"})
        warnings.append("液体式未计粘度修正 Kv 与背压修正 Kp；粘稠/高压差工况需复核。")

    selected_letter = payload.get("orifice_letter")
    selected_area = None
    if device == "psv":
        if selected_letter:
            selected_area = _orifice_mm2(str(selected_letter))
            if selected_area is None:
                raise ValueError("orifice_letter 必须是 API 526 标准孔径 D~T")
        else:
            for name, area_in2 in ORIFICES_IN2:
                if area_in2 * MM2_PER_IN2 >= area_mm2:
                    selected_letter, selected_area = name, area_in2 * MM2_PER_IN2
                    break
        if selected_area is None:
            warnings.append("所需面积超过 API 526 T 型孔径，需多阀并联或非标设计")
    else:
        selected_area = _num(payload.get("disc_area_mm2"), area_mm2 * 1.1)
        selected_letter = "RD"

    if selected_area:
        capacity_kg_s = kd * kb * kc * (mass_flux if fluid in {"gas", "vapor"} else 0.0) * selected_area / 1e6 if fluid in {"gas", "vapor"} else None
        utilization = (w_kg_s / capacity_kg_s) if capacity_kg_s else None
        checks.append({"name": "泄放能力", "requirement": "W_required ≤ W_capacity",
                       "value": f"{utilization*100:.1f}%" if utilization else "液体不校核",
                       "passed": bool(utilization is None or utilization <= 1.0)})
    else:
        utilization = None
        checks.append({"name": "泄放能力", "requirement": "需选型", "value": "超出标准孔径", "passed": False})

    accumulated = set_pressure * (1.0 + overpressure)
    allowed = design_pressure * (1.21 if scenario == "fire" else 1.10)
    checks.append({"name": "积聚压力", "requirement": f"≤ {allowed:.3f} MPa(G)（{('121%' if scenario == 'fire' else '110%')} 设计压力）",
                   "value": f"{accumulated:.3f} MPa(G)", "passed": accumulated <= allowed + 1e-9})
    if device == "psv":
        limit = 0.30 if valve_type == "bellows" else 0.10
        checks.append({"name": "背压", "requirement": f"≤ {limit*100:.0f}% 设定压力（{valve_type}）",
                       "value": f"{backpressure/set_pressure*100:.1f}%", "passed": backpressure <= limit * set_pressure + 1e-9})
    else:
        checks.append({"name": "爆破片背压", "requirement": "按厂家/标准确认", "value": f"{backpressure:.3f} MPa(G)", "passed": True})
    checks.append({"name": "设定压力", "requirement": "≤ 设计压力", "value": f"{set_pressure:.3f} ≤ {design_pressure:.3f} MPa(G)", "passed": set_pressure <= design_pressure + 1e-9})

    residual = None
    if utilization is not None and utilization > 0:
        residual = abs(1.0 - utilization) / utilization
    passed = all(check["passed"] for check in checks) and selected_area is not None
    result.update({
        "selected_orifice": selected_letter,
        "selected_area_mm2": round(selected_area, 1) if selected_area else None,
        "capacity_utilization": round(utilization, 4) if utilization is not None else None,
        "residual": round(residual, 6) if residual is not None else None,
        "checks": checks, "passed": passed,
        "steps": steps, "warnings": warnings,
        "sources": SOURCES, "limits": LIMITS,
    })
    return result


def _rows(result: dict) -> dict:
    inputs = [
        ["设备位号", str(result.get("equipment_tag") or "—")],
        ["阀门/爆破片", result["device_type"]],
        ["工况", result["scenario"]],
        ["介质相态", result["fluid"]],
        ["设定压力", f"{result['set_pressure_mpag']:.3f} MPa(G)"],
        ["设计压力", f"{result['design_pressure_mpag']:.3f} MPa(G)"],
        ["背压", f"{result['backpressure_mpag']:.3f} MPa(G)"],
        ["超压系数", f"{result['overpressure_fraction']*100:.0f}%"],
        ["泄放温度", f"{result.get('temperature_K', '—')} K"],
        ["分子量", f"{result.get('molecular_weight', '—')} kg/kmol"],
        ["绝热指数 k", str(result.get("k", "—"))],
        ["压缩因子 Z", str(result.get("compressibility", "—"))],
        ["Kd / Kb / Kc", f"{result['kd']:.3f} / {result['kb']:.3f} / {result['kc']:.3f}"],
    ]
    results = [
        ["泄放工况", result.get("regime", "—")],
        ["临界压力比", str(result.get("critical_ratio", "—"))],
        ["质量通量", f"{result.get('mass_flux_kg_m2s', '—')} kg/(m2·s)"],
        ["泄放量", f"{result.get('relief_flow_kg_h', '—')} kg/h"],
        ["所需面积", f"{result.get('required_area_mm2', '—')} mm2"],
        ["选定孔径", f"{result.get('selected_orifice', '—')} / {result.get('selected_area_mm2', '—')} mm2"],
        ["能力利用率", f"{result['capacity_utilization']*100:.1f}%" if result.get("capacity_utilization") else "—"],
        ["残差 |1-利用率|", f"{result['residual']:.2e}" if result.get("residual") is not None else "—"],
        ["结论", "通过（方案筛选）" if result["passed"] else "未通过，需调整"],
    ]
    return {"inputs": inputs, "results": results}


def render_docx(path: Path, result: dict, meta: dict) -> None:
    from docx import Document
    from docx.shared import Pt

    document = Document()
    document.add_heading("安全阀 / 爆破片选型计算书", level=1)
    document.add_paragraph(f"项目：{meta.get('project') or '—'}    设备：{result.get('equipment_tag') or '—'}")
    document.add_paragraph("本计算为方案筛选，不替代厂家选型与 API/GB 正式校核；API 原文未取得，公式待核对。")

    def table(title, headers, rows):
        document.add_heading(title, level=2)
        target = document.add_table(rows=1, cols=len(headers))
        target.style = "Table Grid"
        for index, header in enumerate(headers):
            cell = target.rows[0].cells[index]
            cell.text = header
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                    run.font.size = Pt(8)
        for row in rows:
            cells = target.add_row().cells
            for index, value in enumerate(row):
                cells[index].text = str(value)
                for paragraph in cells[index].paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(8)

    table("输入条件", ["项目", "数值/说明"], _rows(result)["inputs"])
    table("计算结果", ["项目", "数值/说明"], _rows(result)["results"])
    table("计算步骤", ["步骤", "公式", "结果", "来源"],
          [[step["step"], step["formula"], step["value"], step["source"]] for step in result["steps"]])
    table("校核", ["项目", "要求", "实际", "结论"],
          [[check["name"], check["requirement"], check["value"], "通过" if check["passed"] else "未通过"]
           for check in result["checks"]])
    document.add_heading("来源与限制", level=2)
    for source in result["sources"]:
        document.add_paragraph("· " + source, style="List Bullet")
    for limit in result["limits"]:
        document.add_paragraph("· " + limit, style="List Bullet")
    document.save(path)


def render_pdf(path: Path, result: dict, meta: dict) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font_name = "Helvetica"
    for candidate in (Path(r"C:\Windows\Fonts\simhei.ttf"), Path(r"C:\Windows\Fonts\Deng.ttf"),
                      Path(r"C:\Windows\Fonts\msyh.ttc")):
        if not candidate.is_file():
            continue
        try:
            if "ChemCJK" not in pdfmetrics.getRegisteredFontNames():
                font = TTFont("ChemCJK", str(candidate), subfontIndex=0) if candidate.suffix.lower() == ".ttc" else TTFont("ChemCJK", str(candidate))
                pdfmetrics.registerFont(font)
            font_name = "ChemCJK"
            break
        except Exception:
            continue

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], fontName=font_name, fontSize=16)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=font_name, fontSize=11, spaceBefore=8)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName=font_name, fontSize=8.5, leading=12)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=10)

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm, title="安全阀选型计算书")
    story = [Paragraph("安全阀 / 爆破片选型计算书", title_style),
             Paragraph(f"项目：{meta.get('project') or '—'}    设备：{result.get('equipment_tag') or '—'}", body),
             Paragraph("本计算为方案筛选，不替代厂家选型与 API/GB 正式校核；API 原文未取得，公式待核对。", small),
             Spacer(1, 4)]

    def make_table(title, headers, rows):
        story.append(Paragraph(title, h2))
        data = [[Paragraph(f"<b>{header}</b>", small) for header in headers]]
        for row in rows:
            data.append([Paragraph(str(cell), small) for cell in row])
        widths = [35 * mm, 55 * mm, 55 * mm, 30 * mm] if len(headers) == 4 else [55 * mm, 120 * mm]
        table = Table(data, repeatRows=1, colWidths=widths)
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE8F4")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(table)
        story.append(Spacer(1, 3))

    make_table("输入条件", ["项目", "数值/说明"], _rows(result)["inputs"])
    make_table("计算结果", ["项目", "数值/说明"], _rows(result)["results"])
    make_table("计算步骤", ["步骤", "公式", "结果", "来源"],
               [[step["step"], step["formula"], step["value"], step["source"]] for step in result["steps"]])
    make_table("校核", ["项目", "要求", "实际", "结论"],
               [[check["name"], check["requirement"], check["value"], "通过" if check["passed"] else "未通过"]
                for check in result["checks"]])
    story.append(Paragraph("来源与限制", h2))
    for source in result["sources"]:
        story.append(Paragraph("· " + source, small))
    for limit in result["limits"]:
        story.append(Paragraph("· " + limit, small))
    doc.build(story)


def build_relief_files(payload: dict, out_dir: Path) -> dict:
    result = compute(payload)
    result["equipment_tag"] = str(payload.get("equipment_tag") or "")
    meta = {"project": str(payload.get("project") or "")}
    out_dir.mkdir(parents=True, exist_ok=True)
    docx = out_dir / "安全阀选型计算书.docx"
    pdf = out_dir / "安全阀选型计算书.pdf"
    json_path = out_dir / "安全阀选型计算书.json"
    render_docx(docx, result, meta)
    render_pdf(pdf, result, meta)
    json_path.write_text(json.dumps({"input": payload, "result": result}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return {"result": result, "docx": docx, "pdf": pdf, "json": json_path}
