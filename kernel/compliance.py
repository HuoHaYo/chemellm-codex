"""Standard-clause compliance matrix.

Builds a clause-by-clause compliance checklist from a curated ledger of the
project's standards, filling status from project data where a deterministic
check exists, otherwise from explicit answers, otherwise "待确认".

Original availability is kept explicit: clauses whose standard text is not in
the workspace are marked "待核对" and never filled with invented limits.
Outputs XLSX, DOCX and JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

STATUSES = ["符合", "不符合", "待确认", "待核对"]

# Curated ledger: only values already verified from the registered PDFs are given
# numeric limits. Entries with availability="待核对" deliberately carry no limit.
LEDGER = [
    {"id": "GBT3915-PURITY", "standard": "GB/T 3915-2021", "title": "工业用苯乙烯", "clause": "表1",
     "requirement": "聚合级苯乙烯纯度（质量分数）≥99.8%", "page": 4, "availability": "已核对原文",
     "check": {"field": "product_purity", "op": "min", "limit": 0.998, "unit": "1"}},
    {"id": "GBT3915-EB", "standard": "GB/T 3915-2021", "title": "工业用苯乙烯", "clause": "表1",
     "requirement": "乙苯 ≤0.08%（质量分数）", "page": 4, "availability": "已核对原文",
     "check": {"field": "product_eb", "op": "max", "limit": 0.0008, "unit": "1"}},
    {"id": "GBT3915-POLYMER", "standard": "GB/T 3915-2021", "title": "工业用苯乙烯", "clause": "表1",
     "requirement": "聚合物 ≤10 mg/kg", "page": 4, "availability": "已核对原文",
     "check": {"field": "product_polymer", "op": "max", "limit": 10, "unit": "mg/kg"}},
    {"id": "GBT3915-PEROXIDE", "standard": "GB/T 3915-2021", "title": "工业用苯乙烯", "clause": "表1",
     "requirement": "过氧化物（以H2O2计）≤50 mg/kg", "page": 4, "availability": "已核对原文",
     "check": {"field": "product_peroxide", "op": "max", "limit": 50, "unit": "mg/kg"}},
    {"id": "GBT3915-ALDEHYDE", "standard": "GB/T 3915-2021", "title": "工业用苯乙烯", "clause": "表1",
     "requirement": "总醛（以苯甲醛计）≤100 mg/kg", "page": 4, "availability": "已核对原文",
     "check": {"field": "product_aldehyde", "op": "max", "limit": 100, "unit": "mg/kg"}},
    {"id": "GBT3915-COLOR", "standard": "GB/T 3915-2021", "title": "工业用苯乙烯", "clause": "表1",
     "requirement": "色度（铂-钴）≤10 Hazen", "page": 4, "availability": "已核对原文",
     "check": {"field": "product_color", "op": "max", "limit": 10, "unit": "Hazen"}},
    {"id": "GBT3915-TBC", "standard": "GB/T 3915-2021", "title": "工业用苯乙烯", "clause": "表1",
     "requirement": "阻聚剂（TBC）10~15 mg/kg", "page": 4, "availability": "已核对原文",
     "check": {"field": "product_tbc", "op": "range", "limit": [10, 15], "unit": "mg/kg"}},
    {"id": "GBT150-SCOPE-P", "standard": "GB/T 150.1-2024", "title": "压力容器 第1部分：通用要求", "clause": "第1章",
     "requirement": "适用于设计压力不大于 35 MPa 的钢制容器", "page": 7, "availability": "已核对原文",
     "check": {"field": "design_pressure_mpa", "op": "max", "limit": 35.0, "unit": "MPa"}},
    {"id": "GBT150-SCOPE-T", "standard": "GB/T 150.1-2024", "title": "压力容器 第1部分：通用要求", "clause": "第1章",
     "requirement": "适用于设计温度 −269~900 ℃ 的钢制容器", "page": 7, "availability": "已核对原文",
     "check": {"field": "design_temperature_c", "op": "range", "limit": [-269.0, 900.0], "unit": "℃"}},
    {"id": "GB36894-SCOPE", "standard": "GB 36894-2018", "title": "危险化学品生产装置和储存设施风险基准", "clause": "第1章",
     "requirement": "适用于危化品生产/储存设施选址与周边土地使用规划的风险判定", "page": 3,
     "availability": "已核对原文", "check": None},
    {"id": "GB36894-INDIVIDUAL", "standard": "GB 36894-2018", "title": "危险化学品生产装置和储存设施风险基准", "clause": "表2",
     "requirement": "周边防护目标个人风险不应超过表2基准（新建/改扩建：3e-7/3e-6/1e-5 a-1）", "page": 5,
     "availability": "已核对原文", "check": {"field": "qra_done", "op": "flag", "limit": True, "unit": "—"}},
    {"id": "GB36894-SOCIETAL", "standard": "GB 36894-2018", "title": "危险化学品生产装置和储存设施风险基准", "clause": "第4章/图1",
     "requirement": "社会风险按 F-N 曲线判定（不可接受区/尽可能降低区/可接受区）", "page": 6,
     "availability": "已核对原文", "check": {"field": "qra_done", "op": "flag", "limit": True, "unit": "—"}},
    {"id": "GB18218-DEF", "standard": "GB 18218-2018", "title": "危险化学品重大危险源辨识", "clause": "第4章",
     "requirement": "按生产/储存单元辨识；数量等于或超过临界量即定为重大危险源", "page": 6,
     "availability": "已核对原文", "check": None},
    {"id": "GB18218-H2", "standard": "GB 18218-2018", "title": "危险化学品重大危险源辨识", "clause": "表1 序号51",
     "requirement": "氢临界量 5 t", "page": 6, "availability": "已核对原文",
     "check": {"field": "inventory_h2_t", "op": "max", "limit": 5.0, "unit": "t"}},
    {"id": "GB18218-STYRENE", "standard": "GB 18218-2018", "title": "危险化学品重大危险源辨识", "clause": "表1 序号58",
     "requirement": "苯乙烯临界量 500 t", "page": 6, "availability": "已核对原文",
     "check": {"field": "inventory_styrene_t", "op": "max", "limit": 500.0, "unit": "t"}},
    {"id": "GB18218-BENZENE", "standard": "GB 18218-2018", "title": "危险化学品重大危险源辨识", "clause": "表1 序号57",
     "requirement": "苯临界量 50 t", "page": 6, "availability": "已核对原文",
     "check": {"field": "inventory_benzene_t", "op": "max", "limit": 50.0, "unit": "t"}},
    {"id": "GB31571-COD", "standard": "GB 31571-2015", "title": "石油化学工业污染物排放标准", "clause": "表1",
     "requirement": "废水 COD 直接排放限值 60 mg/L", "page": 11, "availability": "已核对原文",
     "check": {"field": "emission_cod", "op": "max", "limit": 60.0, "unit": "mg/L"}},
    {"id": "GB31571-NH3", "standard": "GB 31571-2015", "title": "石油化学工业污染物排放标准", "clause": "表1",
     "requirement": "废水氨氮直接排放限值 8.0 mg/L", "page": 11, "availability": "已核对原文",
     "check": {"field": "emission_nh3", "op": "max", "limit": 8.0, "unit": "mg/L"}},
    {"id": "GB31571-EB-WATER", "standard": "GB 31571-2015", "title": "石油化学工业污染物排放标准", "clause": "表3",
     "requirement": "废水乙苯 0.4 mg/L", "page": 13, "availability": "已核对原文",
     "check": {"field": "emission_eb_water", "op": "max", "limit": 0.4, "unit": "mg/L"}},
    {"id": "GB31571-STYRENE-WATER", "standard": "GB 31571-2015", "title": "石油化学工业污染物排放标准", "clause": "表3",
     "requirement": "废水苯乙烯 0.2 mg/L", "page": 13, "availability": "已核对原文",
     "check": {"field": "emission_styrene_water", "op": "max", "limit": 0.2, "unit": "mg/L"}},
    {"id": "GB31571-NMHC", "standard": "GB 31571-2015", "title": "石油化学工业污染物排放标准", "clause": "表4",
     "requirement": "其他有机废气非甲烷总烃 120 mg/m3（或去除效率≥95%）", "page": 14, "availability": "已核对原文",
     "check": {"field": "emission_nmhc", "op": "max", "limit": 120.0, "unit": "mg/m3"}},
    {"id": "GB31571-EB-AIR", "standard": "GB 31571-2015", "title": "石油化学工业污染物排放标准", "clause": "表6",
     "requirement": "废气乙苯 100 mg/m3", "page": 16, "availability": "已核对原文",
     "check": {"field": "emission_eb_air", "op": "max", "limit": 100.0, "unit": "mg/m3"}},
    {"id": "GB31571-STYRENE-AIR", "standard": "GB 31571-2015", "title": "石油化学工业污染物排放标准", "clause": "表6",
     "requirement": "废气苯乙烯 50 mg/m3", "page": 16, "availability": "已核对原文",
     "check": {"field": "emission_styrene_air", "op": "max", "limit": 50.0, "unit": "mg/m3"}},
    {"id": "HGT20519-PID", "standard": "HG/T 20519.1~.6-2009", "title": "化工工艺设计施工图内容和深度统一规定", "clause": "第2部分 工艺系统",
     "requirement": "P&ID 内容深度（符号、位号、管线号、仪表）按系列规定", "page": "—",
     "availability": "待核对", "check": None},
    {"id": "HGT20519-DEPTH", "standard": "HG/T 20519.1~.6-2009", "title": "化工工艺设计施工图内容和深度统一规定", "clause": "第3~6部分",
     "requirement": "设备布置/管道布置/管道机械/管道材料的施工图深度", "page": "—",
     "availability": "待核对", "check": None},
    {"id": "HGT20570-PSV", "standard": "HG/T 20570.2-95", "title": "工艺系统工程设计技术规定 第2部分：安全阀的设置和选用", "clause": "第1章",
     "requirement": "适用于 >0.2 MPa 压力容器防超压安全阀设置与计算", "page": 5,
     "availability": "已核对原文（扫描页）",
     "check": {"field": "relief_set_pressure_mpag", "op": "min", "limit": 0.2, "unit": "MPa"}},
    {"id": "PROJ-HAZOP", "standard": "项目要求", "title": "安全分析", "clause": "—",
     "requirement": "完成 HAZOP/LOPA 工作表", "page": "—", "availability": "项目要求",
     "check": {"field": "hazop_done", "op": "flag", "limit": True, "unit": "—"}},
    {"id": "PROJ-PID", "standard": "项目要求", "title": "图纸交付", "clause": "—",
     "requirement": "完成 A1 P&ID 及数据链（管线表/MTO/IO/C&E）", "page": "—", "availability": "项目要求",
     "check": {"field": "pid_done", "op": "flag", "limit": True, "unit": "—"}},
    {"id": "PROJ-DATASHEET", "standard": "项目要求", "title": "设备交付", "clause": "—",
     "requirement": "完成设备数据表/规格书", "page": "—", "availability": "项目要求",
     "check": {"field": "datasheet_done", "op": "flag", "limit": True, "unit": "—"}},
]


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_value(value: float, unit: str) -> str:
    if unit == "1":
        return f"{value * 100:.3f}%（质量分数）"
    return f"{value:G} {unit}".strip()


def _evaluate(check: dict | None, data: dict, availability: str) -> tuple[str, str]:
    """Return (status, evidence)."""
    if check is None:
        if availability == "待核对":
            return "待核对", "未取得标准原文，条款与数值待核对"
        return "待确认", "需结合项目文件人工确认"
    field = check.get("field")
    op = check.get("op")
    limit = check.get("limit")
    unit = check.get("unit", "")
    if field is None or field not in data:
        return ("待核对", "未取得标准原文，数值待核对") if availability == "待核对" else ("待确认", f"缺少项目数据：{field}")
    raw = data.get(field)
    if op == "flag":
        done = bool(raw)
        return ("符合", "已完成（仍需专业审查/签署）") if done else ("待确认", "尚未完成")
    value = _num(raw)
    if value is None:
        return "待确认", f"项目数据非数值：{field}={raw}"
    if field.startswith("inventory_"):
        if value <= 0:
            return "待确认", f"未提供有效的 {field} 在线量"
        if value > float(limit):
            return "待确认", (f"实际量 {value:G} {unit} ≥ 临界量 {limit} {unit}："
                          "构成/可能构成重大危险源，需按 GB 18218 管理")
        return "符合", f"实际量 {value:G} {unit} < 临界量 {limit} {unit}"
    if op == "min":
        ok = value >= float(limit)
        limit_text = f"≥{limit} {unit}".strip() if unit != "1" else f"≥{float(limit) * 100:.3f}%"
    elif op == "max":
        ok = value <= float(limit)
        limit_text = f"≤{limit} {unit}".strip() if unit != "1" else f"≤{float(limit) * 100:.3f}%"
    elif op == "range":
        low, high = limit
        ok = low <= value <= high
        if unit == "1":
            limit_text = f"{low * 100:.3f}%~{high * 100:.3f}%"
        else:
            limit_text = f"{low}~{high} {unit}".strip()
    else:
        return "待确认", "未知检查类型"
    return ("符合" if ok else "不符合"), f"实际 {_format_value(value, unit)}；限值 {limit_text}"


def _validate(payload: dict, ledger: list[dict]) -> list[str]:
    errors: list[str] = []
    if not isinstance(ledger, list) or not ledger:
        return ["ledger 必须是非空数组"]
    seen = set()
    for index, clause in enumerate(ledger):
        clause_id = str(clause.get("id") or "").strip()
        if not clause_id:
            errors.append(f"ledger[{index}]：缺少 id")
        elif clause_id in seen:
            errors.append(f"ledger[{index}]：条款 id 重复 {clause_id}")
        else:
            seen.add(clause_id)
        if not str(clause.get("standard") or "").strip():
            errors.append(f"{clause_id or index}：缺少 standard")
        if not str(clause.get("requirement") or "").strip():
            errors.append(f"{clause_id or index}：缺少 requirement")
    for clause_id, answer in (payload.get("answers") or {}).items():
        if not isinstance(answer, dict):
            errors.append(f"answers[{clause_id}] 必须是对象")
            continue
        status = str(answer.get("status") or "")
        if status and status not in STATUSES:
            errors.append(f"answers[{clause_id}]：status 必须是 {'/'.join(STATUSES)}")
    return errors


def build_matrix(payload: dict) -> dict:
    ledger = list(payload.get("ledger") or LEDGER)
    errors = _validate(payload, ledger)
    if errors:
        raise ValueError("符合性矩阵参数错误：" + "；".join(errors[:8]))
    data = dict(payload.get("project_data") or {})
    answers = dict(payload.get("answers") or {})
    rows = []
    for index, clause in enumerate(ledger, start=1):
        clause_id = str(clause.get("id"))
        availability = str(clause.get("availability") or "待核对")
        status, evidence = _evaluate(clause.get("check"), data, availability)
        note = ""
        if clause_id in answers:
            answer = answers[clause_id]
            if answer.get("status"):
                status = str(answer["status"])
            if answer.get("evidence"):
                evidence = str(answer["evidence"])
        elif availability == "待核对":
            status = "待核对"
            note = "未取得原文，不填具体限值"
        rows.append({
            "序号": index, "条款ID": clause_id,
            "标准号": str(clause.get("standard")), "标准名称": str(clause.get("title") or ""),
            "条款/表": str(clause.get("clause") or "—"),
            "要求摘要": str(clause.get("requirement")),
            "页码": str(clause.get("page") or "—"),
            "原文状态": availability,
            "项目证据/现状": evidence,
            "判定": status,
            "备注": note or str(clause.get("note") or ""),
        })
    counts = {status: sum(1 for row in rows if row["判定"] == status) for status in STATUSES}
    by_standard: dict[str, dict] = {}
    for row in rows:
        entry = by_standard.setdefault(row["标准号"], {status: 0 for status in STATUSES})
        entry[row["判定"]] += 1
    return {
        "project": str(payload.get("project") or ""),
        "unit": str(payload.get("unit") or ""),
        "rows": rows,
        "counts": counts,
        "by_standard": by_standard,
        "limits": [
            "矩阵只覆盖本项目登记的条款；未取得原文的条款判定为“待核对”，不填写具体限值。",
            "“符合”仅表示现有项目数据满足该数值/完成状态，不代表设计文件已通过专业审核。",
            "涉及排放、QRA、重大危险源与施工图深度的条款，需环评/安全评价/设计审查确认。",
            "标准版本变化时须重新生成矩阵。",
        ],
    }


def _xlsx(path: Path, data: dict) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    fill = PatternFill("solid", fgColor="DCE8F4")
    bold = Font(bold=True)

    def sheet(title, headers, rows):
        target = workbook.create_sheet(title)
        target.append(headers)
        for cell in target[1]:
            cell.font = bold
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for row in rows:
            target.append([row.get(header, "") for header in headers])
        for index, header in enumerate(headers, start=1):
            width = max(10, min(46, max([len(str(header))] + [len(str(row.get(header, ""))) for row in rows]) + 2))
            target.column_dimensions[get_column_letter(index)].width = width
        target.freeze_panes = "A2"

    sheet("符合性矩阵", ["序号", "条款ID", "标准号", "标准名称", "条款/表", "要求摘要", "页码",
                        "原文状态", "项目证据/现状", "判定", "备注"], data["rows"])
    summary_rows = [{"项目": status, "数量": count} for status, count in data["counts"].items()]
    sheet("汇总", ["项目", "数量"], summary_rows)
    standard_rows = []
    for standard, counts in data["by_standard"].items():
        standard_rows.append({"标准号": standard, **counts})
    sheet("按标准汇总", ["标准号", "符合", "不符合", "待确认", "待核对"], standard_rows)
    sheet("说明", ["条目", "内容"], [
        {"条目": "项目/单元", "内容": f"{data['project']} / {data['unit']}"},
        *[{"条目": f"限制{i + 1}", "内容": text} for i, text in enumerate(data["limits"])],
    ])
    if "Sheet" in workbook.sheetnames:
        del workbook["Sheet"]
    workbook.save(path)


def _docx(path: Path, data: dict) -> None:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Cm, Pt

    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    section.left_margin = Cm(1.5)
    section.right_margin = Cm(1.5)
    document.add_heading("标准条款符合性矩阵", level=1)
    document.add_paragraph(f"项目：{data['project'] or '—'}    单元：{data['unit'] or '—'}")
    document.add_paragraph("判定说明：符合=现有项目数据满足；不符合=现有数据超限；待确认=需人工/专业确认；待核对=未取得标准原文。")

    headers = ["序号", "标准号", "条款/表", "要求摘要", "页码", "项目证据/现状", "判定"]
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = header
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
                run.font.size = Pt(7)
    for row in data["rows"]:
        cells = table.add_row().cells
        for index, header in enumerate(headers):
            cells[index].text = str(row.get(header, ""))
            for paragraph in cells[index].paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(6.5)
    document.add_heading("汇总", level=2)
    for status, count in data["counts"].items():
        document.add_paragraph(f"{status}：{count}")
    document.add_heading("限制与说明", level=2)
    for text in data["limits"]:
        document.add_paragraph("· " + text)
    document.save(path)


def build_compliance_files(payload: dict, out_dir: Path) -> dict:
    data = build_matrix(payload)
    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx = out_dir / "标准符合性矩阵.xlsx"
    docx = out_dir / "标准符合性矩阵.docx"
    json_path = out_dir / "标准符合性矩阵.json"
    _xlsx(xlsx, data)
    _docx(docx, data)
    json_path.write_text(json.dumps({"input": payload, "matrix": data}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return {"data": data, "xlsx": xlsx, "docx": docx, "json": json_path}
