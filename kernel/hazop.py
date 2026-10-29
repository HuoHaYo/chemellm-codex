"""HAZOP / LOPA worksheet generator.

Builds a HAZOP deviation matrix from process nodes (or from P&ID tags) and a
LOPA scenario table with independent protection layers, mitigated frequency,
required risk reduction factor and an initial SIL estimate.

Everything is traceable: each row keeps the node, parameter, guide word, causes,
consequences, safeguards, recommendations, severity/likelihood; LOPA rows keep
the initiating event, IPL PFDs, target frequency and the calculation. Values not
provided are marked "待分析/待确认" rather than invented.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

GUIDE_WORDS = ["无", "高", "低", "反向", "异常", "过量"]
PARAMETERS = ["压力", "温度", "流量", "液位", "组成"]
SEVERITY_LEVELS = {"minor": "轻微", "moderate": "较大", "major": "重大", "catastrophic": "灾难"}
DEFAULT_TARGET = {"minor": 1e-4, "moderate": 1e-5, "major": 1e-6, "catastrophic": 1e-7}


def _num(value, default=None):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=None):
    number = _num(value, default)
    return int(number) if number is not None else None


def _risk(severity: int, likelihood: int) -> str:
    score = severity * likelihood
    if score >= 15:
        return "极高"
    if score >= 9:
        return "高"
    if score >= 4:
        return "中"
    return "低"


def _sil(rrf: float) -> dict:
    if rrf <= 0:
        return {"required_rrf": None, "sil": "—"}
    if rrf < 10:
        return {"required_rrf": round(rrf, 2), "sil": "无（<SIL1）"}
    if rrf < 100:
        return {"required_rrf": round(rrf, 2), "sil": "SIL1"}
    if rrf < 1000:
        return {"required_rrf": round(rrf, 2), "sil": "SIL2"}
    if rrf < 10000:
        return {"required_rrf": round(rrf, 2), "sil": "SIL3"}
    return {"required_rrf": round(rrf, 2), "sil": "SIL4"}


def _validate(payload: dict) -> list[str]:
    errors: list[str] = []
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ["nodes 必须是非空数组"]
    seen = set()
    for index, node in enumerate(nodes):
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            errors.append(f"nodes[{index}]：缺少 id")
        elif node_id in seen:
            errors.append(f"nodes[{index}]：节点 id 重复 {node_id}")
        else:
            seen.add(node_id)
        if not str(node.get("name") or "").strip():
            errors.append(f"{node_id or index}：缺少 name")
        parameters = node.get("parameters")
        if parameters is not None and (not isinstance(parameters, list) or not parameters):
            errors.append(f"{node_id}：parameters 必须是非空数组")
        for deviation in node.get("deviations") or []:
            guide_word = str(deviation.get("guide_word") or "")
            if guide_word and guide_word not in GUIDE_WORDS:
                errors.append(f"{node_id}：不支持的引导词 {guide_word}")
            parameter = str(deviation.get("parameter") or "")
            if parameter and parameter not in PARAMETERS:
                errors.append(f"{node_id}：不支持的参数 {parameter}")
            severity = _int(deviation.get("severity"))
            likelihood = _int(deviation.get("likelihood"))
            if severity is not None and not (1 <= severity <= 5):
                errors.append(f"{node_id}：severity 应为 1~5")
            if likelihood is not None and not (1 <= likelihood <= 5):
                errors.append(f"{node_id}：likelihood 应为 1~5")
    for index, scenario in enumerate(payload.get("lopa") or []):
        frequency = _num(scenario.get("frequency_per_year"))
        if not frequency or frequency <= 0:
            errors.append(f"lopa[{index}]：frequency_per_year 必须 >0")
        target = _num(scenario.get("target_frequency_per_year"))
        if target is not None and target <= 0:
            errors.append(f"lopa[{index}]：target_frequency_per_year 必须 >0")
        for ipl_index, ipl in enumerate(scenario.get("ipls") or []):
            pfd = _num(ipl.get("pfd"))
            if pfd is None or not (0 < pfd <= 1):
                errors.append(f"lopa[{index}].ipls[{ipl_index}]：pfd 必须在 (0,1]")
    return errors


def build_worksheet(payload: dict) -> dict:
    errors = _validate(payload)
    if errors:
        raise ValueError("HAZOP/LOPA 参数错误：" + "；".join(errors[:8]))
    nodes = list(payload.get("nodes") or [])
    hazop_rows: list[dict] = []
    node_rows: list[dict] = []
    warnings: list[str] = []
    for node in nodes:
        node_id = str(node.get("id"))
        parameters = list(node.get("parameters") or PARAMETERS)
        node_rows.append({
            "节点": node_id, "名称": str(node.get("name") or ""),
            "设计意图": str(node.get("intent") or "待补充"),
            "涉及设备/管线": "、".join(node.get("tags") or []) or "待补充",
            "参数": "、".join(parameters),
        })
        deviations = node.get("deviations")
        if deviations:
            for deviation in deviations:
                severity = _int(deviation.get("severity"), 3)
                likelihood = _int(deviation.get("likelihood"), 3)
                hazop_rows.append({
                    "节点": node_id, "参数": str(deviation.get("parameter") or "—"),
                    "引导词": str(deviation.get("guide_word") or "—"),
                    "偏差": str(deviation.get("deviation") or f"{deviation.get('guide_word','')}{deviation.get('parameter','')}"),
                    "原因": "；".join(deviation.get("causes") or []) or "待分析",
                    "后果": "；".join(deviation.get("consequences") or []) or "待分析",
                    "现有措施": "；".join(deviation.get("safeguards") or []) or "待分析",
                    "建议措施": "；".join(deviation.get("recommendations") or []) or "—",
                    "严重度": severity, "可能性": likelihood,
                    "风险等级": _risk(severity, likelihood),
                    "状态": "待评审" if not deviation.get("causes") else "已分析",
                })
        else:
            for parameter in parameters:
                for guide_word in GUIDE_WORDS[:4]:
                    hazop_rows.append({
                        "节点": node_id, "参数": parameter, "引导词": guide_word,
                        "偏差": f"{guide_word}{parameter}", "原因": "待分析", "后果": "待分析",
                        "现有措施": "待分析", "建议措施": "—",
                        "严重度": "", "可能性": "", "风险等级": "待分析", "状态": "骨架行",
                    })
            warnings.append(f"{node_id}：未提供 deviations，已生成偏差矩阵骨架行（需 HAZOP 会议填充）")

    lopa_rows: list[dict] = []
    for index, scenario in enumerate(payload.get("lopa") or []):
        frequency = _num(scenario.get("frequency_per_year"))
        severity = str(scenario.get("severity") or "major")
        target = _num(scenario.get("target_frequency_per_year"), DEFAULT_TARGET.get(severity, 1e-6))
        pfd_product = 1.0
        included = []
        for ipl in scenario.get("ipls") or []:
            pfd = _num(ipl.get("pfd"))
            independent = ipl.get("independent", True)
            if independent is False:
                warnings.append(f"lopa[{index}]：IPL“{ipl.get('name')}”标记为不独立，未计入风险降低")
                continue
            pfd_product *= pfd
            included.append(f"{ipl.get('name')}(PFD={pfd:g})")
        mitigated = frequency * pfd_product
        rrf = frequency / target if target else None
        sil = _sil(rrf) if rrf else {"required_rrf": None, "sil": "—"}
        passed = mitigated <= target + 1e-15
        lopa_rows.append({
            "场景": str(scenario.get("id") or f"L-{index + 1:02d}"),
            "节点": str(scenario.get("node") or "—"),
            "描述": str(scenario.get("scenario") or ""),
            "初始事件": str(scenario.get("initiating_event") or ""),
            "初始频率(/a)": f"{frequency:.2e}",
            "独立保护层": "；".join(included) or "无",
            "PFD 乘积": f"{pfd_product:.2e}",
            "缓解后频率(/a)": f"{mitigated:.2e}",
            "目标频率(/a)": f"{target:.2e}",
            "所需 RRF": sil["required_rrf"],
            "初判 SIL": sil["sil"],
            "结论": "满足" if passed else "不满足，需增加保护层",
        })
        if not passed:
            warnings.append(f"lopa[{index}]：缓解后频率 {mitigated:.2e}/a 高于目标 {target:.2e}/a")

    return {
        "project": str(payload.get("project") or ""),
        "unit": str(payload.get("unit") or ""),
        "nodes": node_rows,
        "hazop": hazop_rows,
        "lopa": lopa_rows,
        "summary": {
            "nodes": len(node_rows), "hazop_rows": len(hazop_rows),
            "analysed_rows": sum(1 for row in hazop_rows if row["状态"] == "已分析"),
            "lopa_scenarios": len(lopa_rows),
            "lopa_passed": sum(1 for row in lopa_rows if row["结论"] == "满足"),
        },
        "warnings": warnings,
        "limits": [
            "HAZOP 为会议驱动方法；本表为结构化工作表，不替代 HAZOP 会议与签署。",
            "LOPA 的初始频率、PFD、目标频率与严重度需企业风险标准/项目数据确认。",
            "SIL 初判仅按 RRF 分级，未做 SIL 验证（PFD 计算、架构约束、共因失效）。",
            "未提供的偏差原因/后果标注“待分析”，不得据此关闭建议措施。",
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
            width = max(9, min(30, max([len(str(header))] + [len(str(row.get(header, ""))) for row in rows]) + 2))
            target.column_dimensions[get_column_letter(index)].width = width
        target.freeze_panes = "A2"

    sheet("节点清单", ["节点", "名称", "设计意图", "涉及设备/管线", "参数"], data["nodes"])
    sheet("HAZOP工作表", ["节点", "参数", "引导词", "偏差", "原因", "后果", "现有措施", "建议措施",
                          "严重度", "可能性", "风险等级", "状态"], data["hazop"])
    sheet("LOPA表", ["场景", "节点", "描述", "初始事件", "初始频率(/a)", "独立保护层", "PFD 乘积",
                     "缓解后频率(/a)", "目标频率(/a)", "所需 RRF", "初判 SIL", "结论"], data["lopa"])
    sheet("说明", ["项目", "内容"], [
        {"项目": "项目/单元", "内容": f"{data['project']} / {data['unit']}"},
        {"项目": "汇总", "内容": json.dumps(data["summary"], ensure_ascii=False)},
        *[{"项目": f"限制{i + 1}", "内容": text} for i, text in enumerate(data["limits"])],
    ])
    if "Sheet" in workbook.sheetnames:
        del workbook["Sheet"]
    workbook.save(path)


def _docx(path: Path, data: dict) -> None:
    from docx import Document
    from docx.shared import Pt

    document = Document()
    document.add_heading("HAZOP / LOPA 分析工作表", level=1)
    document.add_paragraph(f"项目：{data['project'] or '—'}    单元：{data['unit'] or '—'}")
    document.add_paragraph("本文件为结构化工作表，不替代 HAZOP 会议、LOPA 评审与签署。")

    def table(title, headers, rows):
        document.add_heading(title, level=2)
        if not rows:
            document.add_paragraph("（无数据）")
            return
        target = document.add_table(rows=1, cols=len(headers))
        target.style = "Table Grid"
        for index, header in enumerate(headers):
            cell = target.rows[0].cells[index]
            cell.text = str(header)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                    run.font.size = Pt(7.5)
        for row in rows:
            cells = target.add_row().cells
            for index, header in enumerate(headers):
                cells[index].text = str(row.get(header, ""))
                for paragraph in cells[index].paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(7.5)

    table("节点清单", ["节点", "名称", "设计意图", "涉及设备/管线", "参数"], data["nodes"])
    table("HAZOP 工作表", ["节点", "参数", "引导词", "偏差", "原因", "后果", "现有措施", "建议措施",
                          "严重度", "可能性", "风险等级", "状态"], data["hazop"])
    table("LOPA 表", ["场景", "节点", "描述", "初始事件", "初始频率(/a)", "独立保护层", "PFD 乘积",
                     "缓解后频率(/a)", "目标频率(/a)", "所需 RRF", "初判 SIL", "结论"], data["lopa"])
    document.add_heading("限制与说明", level=2)
    for text in data["limits"]:
        document.add_paragraph("· " + text)
    document.save(path)


def build_hazop_files(payload: dict, out_dir: Path) -> dict:
    data = build_worksheet(payload)
    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx = out_dir / "HAZOP_LOPA工作表.xlsx"
    docx = out_dir / "HAZOP_LOPA工作表.docx"
    json_path = out_dir / "HAZOP_LOPA数据.json"
    _xlsx(xlsx, data)
    _docx(docx, data)
    json_path.write_text(json.dumps({"input": payload, "worksheet": data}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return {"data": data, "xlsx": xlsx, "docx": docx, "json": json_path}
