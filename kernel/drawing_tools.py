"""Drawing tools: AI-friendly P&ID rendering and parametric equipment CAD.

Two deliverables are implemented here:

1. P&ID: a structured diagram model (nodes/edges + instruments/control valves/
   interlocks/line numbers) is validated and rendered deterministically to
   SVG, PDF, DXF, JSON and a DEXPI-style XML subset.
2. CAD: a parametric equipment solid (vessel/column/tank/exchanger) is built
   by FreeCADCmd and exported as FCStd + STEP, plus an elevation SVG preview.

Both renderers are pure local code: no cloud, no model calls at render time.
The LLM/agent only produces the structured model; geometry and layout are
deterministic and re-checkable.
"""

from __future__ import annotations

import html
import json
import math
import re
import subprocess
from pathlib import Path

# --------------------------------------------------------------------------- #
# P&ID model validation
# --------------------------------------------------------------------------- #
EQUIPMENT_KINDS = {"tank", "column", "reactor", "exchanger", "pump", "vessel"}
INSTRUMENT_KINDS = {"instrument"}
VALVE_KINDS = {"control_valve"}
DRAWING_KINDS = EQUIPMENT_KINDS | INSTRUMENT_KINDS | VALVE_KINDS
LINE_NO_RE = re.compile(r"^[A-Z]{1,6}-\d{2,4}-\d{1,3}-[A-Z0-9]{1,6}$")
INSTRUMENT_TAG_RE = re.compile(r"^(TI|TIC|PI|PIC|FIC|FIQ|LI|LIC|AI|AIC|XI|XIC|FI|PDI|TI)[A-Z]?-?\d+[A-Z]?$")
VALVE_TAG_RE = re.compile(r"^(FV|LV|TV|PV|XV|HV)-?\d+[A-Z]?$")


def validate_pid(model: dict) -> dict:
    blockers: list[str] = []
    warnings: list[str] = []
    nodes = model.get("nodes") or []
    edges = model.get("edges") or []
    if not isinstance(nodes, list) or not nodes:
        blockers.append("缺少 nodes（设备/仪表/阀门）")
        nodes = []
    if not isinstance(edges, list) or not edges:
        blockers.append("缺少 edges（管线/信号/联锁）")
        edges = []

    ids: dict[str, dict] = {}
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            blockers.append("存在缺少 id 的节点")
            continue
        if node_id in ids:
            blockers.append(f"节点 id 重复：{node_id}")
        kind = node.get("kind")
        if kind not in DRAWING_KINDS:
            blockers.append(f"{node_id}：不支持的 kind={kind}")
        if not str(node.get("label") or "").strip():
            warnings.append(f"{node_id}：缺少 label")
        ids[node_id] = node

    for node_id, node in ids.items():
        kind = node.get("kind")
        if kind in INSTRUMENT_KINDS and not INSTRUMENT_TAG_RE.match(node_id):
            warnings.append(f"{node_id}：仪表位号不像 TI/TIC/PI/FIC 等规范位号")
        if kind in VALVE_KINDS and not VALVE_TAG_RE.match(node_id):
            warnings.append(f"{node_id}：控制阀位号不像 FV/LV/TV/PV 等规范位号")

    line_numbers: dict[str, str] = {}
    signal_edges: dict[str, int] = {node_id: 0 for node_id in ids}
    process_edges: dict[str, int] = {node_id: 0 for node_id in ids}
    for index, edge in enumerate(edges):
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        kind = edge.get("kind") or "process"
        if source not in ids:
            blockers.append(f"边 {index}：source 不存在：{source}")
            continue
        if target not in ids:
            blockers.append(f"边 {index}：target 不存在：{target}")
            continue
        if source == target:
            blockers.append(f"边 {index}：source 与 target 相同：{source}")
        source_kind = ids[source].get("kind")
        target_kind = ids[target].get("kind")
        if kind == "process":
            for endpoint, endpoint_kind in ((source, source_kind), (target, target_kind)):
                if endpoint_kind in INSTRUMENT_KINDS:
                    blockers.append(f"边 {index}：工艺管线不能连到仪表 {endpoint}")
            process_edges[source] += 1
            process_edges[target] += 1
            line_no = str(edge.get("line_no") or "").strip()
            if line_no:
                if not LINE_NO_RE.match(line_no):
                    warnings.append(f"边 {index}：管线号格式不像 PG-101-50-A1A：{line_no}")
                if line_no in line_numbers:
                    blockers.append(f"管线号重复：{line_no}（边 {line_numbers[line_no]} 与 {index}）")
                else:
                    line_numbers[line_no] = str(index)
        elif kind == "signal":
            signal_edges[source] += 1
            signal_edges[target] += 1
            if source_kind not in INSTRUMENT_KINDS:
                warnings.append(f"边 {index}：信号线 source {source} 不是仪表")
            if target_kind not in (INSTRUMENT_KINDS | VALVE_KINDS | EQUIPMENT_KINDS):
                blockers.append(f"边 {index}：信号线 target {target} 类型不支持：{target_kind}")
        elif kind == "interlock":
            signal_edges[source] += 1
            signal_edges[target] += 1
            if source_kind not in INSTRUMENT_KINDS:
                warnings.append(f"边 {index}：联锁线 source {source} 不是仪表")
        else:
            blockers.append(f"边 {index}：不支持的 kind={kind}")

    for node_id, node in ids.items():
        kind = node.get("kind")
        if kind in VALVE_KINDS:
            if signal_edges[node_id] == 0:
                blockers.append(f"{node_id}：控制阀没有信号线（需要仪表→阀门）")
            if process_edges[node_id] == 0:
                blockers.append(f"{node_id}：控制阀没有工艺管线")
        if kind in INSTRUMENT_KINDS and (signal_edges[node_id] + process_edges[node_id]) == 0:
            blockers.append(f"{node_id}：仪表没有连接任何线")
        if kind in EQUIPMENT_KINDS and process_edges[node_id] == 0:
            blockers.append(f"{node_id}：设备没有工艺管线连接")

    instruments = [node_id for node_id, node in ids.items() if node.get("kind") in INSTRUMENT_KINDS]
    if not instruments:
        warnings.append("图上没有仪表；若要表达 P&ID，建议补充 TI/PI/FIC 等")
    valves = [node_id for node_id, node in ids.items() if node.get("kind") in VALVE_KINDS]
    if not valves and instruments:
        warnings.append("有仪表但没有控制阀；确认是否需要调节回路")

    return {"passed": not blockers, "blockers": blockers, "warnings": warnings,
            "counts": {"nodes": len(ids), "edges": len(edges), "instruments": len(instruments), "valves": len(valves)}}


# --------------------------------------------------------------------------- #
# P&ID rendering primitives
# --------------------------------------------------------------------------- #
PALETTE = {
    "ink": "#1f3b57",
    "muted": "#5a6b7d",
    "equipment": "#eef4fb",
    "equipment_stroke": "#37506b",
    "instrument": "#ffffff",
    "signal": "#2e7d32",
    "interlock": "#c62828",
    "process": "#1f3b57",
    "paper": "#ffffff",
    "border": "#90a4ae",
}


def _pid_anchor(node: dict) -> tuple[float, float]:
    return float(node.get("x", 0.0)), float(node.get("y", 0.0))


def _pid_symbol(node: dict) -> list[tuple]:
    """Return primitives for a diagram node."""
    x, y = _pid_anchor(node)
    kind = node.get("kind")
    tag = str(node.get("id") or "")
    label = str(node.get("label") or "")
    ink = PALETTE["equipment_stroke"]
    fill = PALETTE["equipment"]
    out: list[tuple] = []
    if kind in {"column"}:
        width, height = 46.0, 150.0
        out.append(("rect", x - width / 2, y - height / 2, width, height, fill, ink, 1.6, 4))
        for step in range(1, 8):
            yy = y - height / 2 + height * step / 8
            out.append(("line", x - width / 2 + 5, yy, x + width / 2 - 5, yy, ink, 0.8, None))
        out.append(("text", x, y - height / 2 - 10, tag, 11, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + height / 2 + 14, label, 10, PALETTE["muted"], "middle", False))
    elif kind in {"reactor", "vessel"}:
        width, height = 64.0, 100.0
        out.append(("rect", x - width / 2, y - height / 2, width, height, fill, ink, 1.6, 8))
        out.append(("line", x, y - height / 2 + 8, x, y + height / 2 - 8, ink, 0.8, None))
        out.append(("line", x - width / 2 + 10, y + 8, x + width / 2 - 10, y + 8, ink, 0.8, None))
        out.append(("text", x, y - height / 2 - 10, tag, 11, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + height / 2 + 14, label, 10, PALETTE["muted"], "middle", False))
    elif kind == "tank":
        width, height = 64.0, 72.0
        out.append(("rect", x - width / 2, y - height / 2, width, height, fill, ink, 1.6, 2))
        out.append(("ellipse", x, y - height / 2, width / 2, 7, fill, ink, 1.2))
        out.append(("text", x, y - height / 2 - 12, tag, 11, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + height / 2 + 14, label, 10, PALETTE["muted"], "middle", False))
    elif kind == "exchanger":
        out.append(("circle", x, y, 30, fill, ink, 1.6))
        out.append(("line", x - 30, y, x + 30, y, ink, 1.0, None))
        out.append(("text", x, y - 34, tag, 11, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + 42, label, 10, PALETTE["muted"], "middle", False))
    elif kind == "pump":
        out.append(("circle", x, y, 24, fill, ink, 1.6))
        out.append(("polygon", [(x - 10, y - 12), (x + 12, y), (x - 10, y + 12)], fill, ink, 1.0))
        out.append(("text", x, y - 30, tag, 11, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + 38, label, 10, PALETTE["muted"], "middle", False))
    elif kind == "instrument":
        out.append(("circle", x, y, 26, PALETTE["instrument"], ink, 1.6))
        match = re.match(r"^([A-Z]+)-?(\d+[A-Z]?)$", tag)
        if match:
            out.append(("text", x, y - 2, match.group(1), 9, PALETTE["ink"], "middle", True))
            out.append(("text", x, y + 11, match.group(2), 8, PALETTE["ink"], "middle", False))
        else:
            out.append(("text", x, y + 4, tag, 9, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + 40, label, 10, PALETTE["muted"], "middle", False))
    elif kind == "control_valve":
        size = 20.0
        out.append(("polygon", [(x - size, y - size / 2), (x, y + size / 2), (x - size, y + size / 2 + 1)], fill, ink, 1.2))
        out.append(("polygon", [(x + size, y - size / 2), (x, y + size / 2), (x + size, y + size / 2 + 1)], fill, ink, 1.2))
        out.append(("line", x, y - size / 2, x, y - size, ink, 1.0, None))
        out.append(("ellipse", x, y - size - 6, 12, 6, PALETTE["instrument"], ink, 1.0))
        out.append(("text", x, y - size - 12, tag, 10, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + size + 14, label, 10, PALETTE["muted"], "middle", False))
    return out


def _arrow(x1: float, y1: float, x2: float, y2: float, color: str) -> list[tuple]:
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 8.0
    left = (x2 - size * math.cos(angle - 0.4), y2 - size * math.sin(angle - 0.4))
    right = (x2 - size * math.cos(angle + 0.4), y2 - size * math.sin(angle + 0.4))
    return [("polygon", [left, right, (x2, y2)], color, color, 0.5)]


def _pid_primitives(model: dict) -> tuple[float, float, list[tuple]]:
    width, height = 1200.0, 900.0
    primitives: list[tuple] = [
        ("rect", 0, 0, width, height, PALETTE["paper"], None, 0.0, 0),
        ("rect", 18, 18, width - 36, height - 36, None, PALETTE["border"], 1.0, 0),
    ]
    title = str(model.get("title") or "P&ID")
    drawing_no = str(model.get("drawing_number") or "PID-001")
    primitives.append(("text", 32, 44, title, 18, PALETTE["ink"], "start", True))
    primitives.append(("text", width - 32, 44, drawing_no, 12, PALETTE["muted"], "end", False))

    nodes = {str(node.get("id")): node for node in (model.get("nodes") or []) if node.get("id")}
    for node in nodes.values():
        primitives.extend(_pid_symbol(node))

    for edge in model.get("edges") or []:
        source = nodes.get(str(edge.get("source")))
        target = nodes.get(str(edge.get("target")))
        if not source or not target:
            continue
        x1, y1 = _pid_anchor(source)
        x2, y2 = _pid_anchor(target)
        kind = edge.get("kind") or "process"
        if kind == "signal":
            primitives.append(("line", x1, y1, x2, y2, PALETTE["signal"], 1.1, "dash"))
        elif kind == "interlock":
            primitives.append(("line", x1, y1, x2, y2, PALETTE["interlock"], 1.4, "dashdot"))
        else:
            primitives.append(("line", x1, y1, x2, y2, PALETTE["process"], 1.6, None))
            primitives.extend(_arrow(x1, y1, x2, y2, PALETTE["process"]))
        label = str(edge.get("label") or "").strip()
        line_no = str(edge.get("line_no") or "").strip()
        text = line_no or label
        if text:
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            primitives.append(("text", mx, my - 4, text, 9, PALETTE["muted"], "middle", False))

    # title block
    bx, by, bw, bh = width - 380.0, height - 120.0, 348.0, 88.0
    primitives.append(("rect", bx, by, bw, bh, PALETTE["paper"], PALETTE["border"], 1.0, 0))
    rows = [
        ("项目", str(model.get("project") or "ChemELLM 本地工作台")),
        ("图号", drawing_no),
        ("比例/单位", "示意（非施工图） / mm"),
        ("说明", "AI/规则生成的结构化图模型渲染；需工程审核"),
    ]
    for index, (key, value) in enumerate(rows):
        yy = by + 20 + index * 18
        primitives.append(("text", bx + 10, yy, key, 9, PALETTE["muted"], "start", False))
        primitives.append(("text", bx + 90, yy, value, 9, PALETTE["ink"], "start", False))
    return width, height, primitives


# --------------------------------------------------------------------------- #
# P&ID renderers
# --------------------------------------------------------------------------- #
def _svg(width: float, height: float, primitives: list[tuple]) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        % (width, height, width, height),
        '<rect width="%d" height="%d" fill="%s"/>' % (width, height, PALETTE["paper"]),
    ]
    for primitive in primitives:
        kind = primitive[0]
        if kind == "line":
            _, x1, y1, x2, y2, color, line_width, dash = primitive
            dash_attr = ""
            if dash == "dash":
                dash_attr = ' stroke-dasharray="6,4"'
            elif dash == "dashdot":
                dash_attr = ' stroke-dasharray="10,3,2,3"'
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%.1f"%s/>'
                         % (x1, y1, x2, y2, color, line_width, dash_attr))
        elif kind == "rect":
            _, x, y, w, h, fill, stroke, line_width, radius = primitive
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" fill="%s" stroke="%s" stroke-width="%.1f"/>'
                         % (x, y, w, h, radius, fill or "none", stroke or "none", line_width))
        elif kind == "circle":
            _, cx, cy, radius, fill, stroke, line_width = primitive
            parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" stroke-width="%.1f"/>'
                         % (cx, cy, radius, fill or "none", stroke or "none", line_width))
        elif kind == "ellipse":
            _, cx, cy, rx, ry, fill, stroke, line_width = primitive
            parts.append('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s" stroke="%s" stroke-width="%.1f"/>'
                         % (cx, cy, rx, ry, fill or "none", stroke or "none", line_width))
        elif kind == "polygon":
            _, points, fill, stroke, line_width = primitive
            data = " ".join("%.1f,%.1f" % (px, py) for px, py in points)
            parts.append('<polygon points="%s" fill="%s" stroke="%s" stroke-width="%.1f"/>'
                         % (data, fill or "none", stroke or "none", line_width))
        elif kind == "text":
            _, x, y, text, size, color, anchor, bold = primitive
            weight = "bold" if bold else "normal"
            parts.append('<text x="%.1f" y="%.1f" font-size="%.1f" fill="%s" text-anchor="%s" font-weight="%s" font-family="Arial, sans-serif">%s</text>'
                         % (x, y, size, color, {"middle": "middle", "end": "end"}.get(anchor, "start"), weight, html.escape(str(text))))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _pdf(width: float, height: float, primitives: list[tuple], path: Path) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as pdf_canvas

    font_name = "Helvetica"
    bold_name = "Helvetica-Bold"
    cjk_candidates = [
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\Deng.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
    ]
    for candidate in cjk_candidates:
        if not candidate.is_file():
            continue
        try:
            if "ChemCJK" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ChemCJK", str(candidate), subfontIndex=0) if candidate.suffix.lower() == ".ttc" else TTFont("ChemCJK", str(candidate)))
            font_name = "ChemCJK"
            bold_name = "ChemCJK"
        except Exception:
            continue
        break

    pdf = pdf_canvas.Canvas(str(path), pagesize=(width, height))
    pdf.setLineCap(1)
    for primitive in primitives:
        kind = primitive[0]
        if kind == "line":
            _, x1, y1, x2, y2, color, line_width, dash = primitive
            pdf.setStrokeColor(HexColor(color))
            pdf.setLineWidth(line_width)
            if dash == "dash":
                pdf.setDash([6, 4], 0)
            elif dash == "dashdot":
                pdf.setDash([10, 3, 2, 3], 0)
            else:
                pdf.setDash()
            pdf.line(x1, height - y1, x2, height - y2)
        elif kind == "rect":
            _, x, y, w, h, fill, stroke, line_width, radius = primitive
            if fill:
                pdf.setFillColor(HexColor(fill))
            if stroke:
                pdf.setStrokeColor(HexColor(stroke))
                pdf.setLineWidth(line_width)
            if radius:
                pdf.roundRect(x, height - y - h, w, h, radius, stroke=1 if stroke else 0, fill=1 if fill else 0)
            else:
                pdf.rect(x, height - y - h, w, h, stroke=1 if stroke else 0, fill=1 if fill else 0)
        elif kind == "circle":
            _, cx, cy, radius, fill, stroke, line_width = primitive
            if fill:
                pdf.setFillColor(HexColor(fill))
            if stroke:
                pdf.setStrokeColor(HexColor(stroke))
                pdf.setLineWidth(line_width)
            pdf.circle(cx, height - cy, radius, stroke=1 if stroke else 0, fill=1 if fill else 0)
        elif kind == "ellipse":
            _, cx, cy, rx, ry, fill, stroke, line_width = primitive
            if fill:
                pdf.setFillColor(HexColor(fill))
            if stroke:
                pdf.setStrokeColor(HexColor(stroke))
                pdf.setLineWidth(line_width)
            pdf.ellipse(cx - rx, height - cy - ry, cx + rx, height - cy + ry, stroke=1 if stroke else 0, fill=1 if fill else 0)
        elif kind == "polygon":
            _, points, fill, stroke, line_width = primitive
            path_obj = pdf.beginPath()
            path_obj.moveTo(points[0][0], height - points[0][1])
            for px, py in points[1:]:
                path_obj.lineTo(px, height - py)
            path_obj.close()
            if fill:
                pdf.setFillColor(HexColor(fill))
            if stroke:
                pdf.setStrokeColor(HexColor(stroke))
                pdf.setLineWidth(line_width)
            pdf.drawPath(path_obj, stroke=1 if stroke else 0, fill=1 if fill else 0)
        elif kind == "text":
            _, x, y, text, size, color, anchor, bold = primitive
            pdf.setFillColor(HexColor(color))
            pdf.setFont(bold_name if bold else font_name, size)
            if anchor == "middle":
                pdf.drawCentredString(x, height - y, str(text))
            elif anchor == "end":
                pdf.drawRightString(x, height - y, str(text))
            else:
                pdf.drawString(x, height - y, str(text))
    pdf.showPage()
    pdf.save()


def _dxf(width: float, height: float, primitives: list[tuple], path: Path) -> None:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for layer, color in (("EQUIPMENT", 5), ("PROCESS", 7), ("SIGNAL", 3), ("INTERLOCK", 1), ("TEXT", 7), ("FRAME", 8)):
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)
    for primitive in primitives:
        kind = primitive[0]
        if kind == "line":
            _, x1, y1, x2, y2, color, line_width, dash = primitive
            layer = "SIGNAL" if color == PALETTE["signal"] else ("INTERLOCK" if color == PALETTE["interlock"] else "PROCESS")
            msp.add_line((x1, height - y1), (x2, height - y2), dxfattribs={"layer": layer})
        elif kind == "rect":
            _, x, y, w, h, fill, stroke, line_width, radius = primitive
            msp.add_lwpolyline([
                (x, height - y), (x + w, height - y), (x + w, height - y - h), (x, height - y - h)
            ], close=True, dxfattribs={"layer": "FRAME" if fill == PALETTE["paper"] else "EQUIPMENT"})
        elif kind == "circle":
            _, cx, cy, radius, fill, stroke, line_width = primitive
            msp.add_circle((cx, height - cy), radius, dxfattribs={"layer": "EQUIPMENT"})
        elif kind == "ellipse":
            _, cx, cy, rx, ry, fill, stroke, line_width = primitive
            msp.add_ellipse((cx, height - cy), major_axis=(rx, 0), ratio=ry / rx if rx else 1.0,
                            dxfattribs={"layer": "EQUIPMENT"})
        elif kind == "polygon":
            _, points, fill, stroke, line_width = primitive
            msp.add_lwpolyline([(px, height - py) for px, py in points], close=True,
                               dxfattribs={"layer": "EQUIPMENT" if fill != PALETTE["process"] else "PROCESS"})
        elif kind == "text":
            _, x, y, text, size, color, anchor, bold = primitive
            entity = msp.add_text(str(text), dxfattribs={"height": max(size * 0.8, 2.0), "layer": "TEXT"})
            alignment = TextEntityAlignment.MIDDLE_CENTER if anchor == "middle" else (
                TextEntityAlignment.MIDDLE_RIGHT if anchor == "end" else TextEntityAlignment.MIDDLE_LEFT)
            entity.set_placement((x, height - y), align=alignment)
    doc.saveas(str(path))


def _dexpi_subset(model: dict) -> str:
    nodes = model.get("nodes") or []
    edges = model.get("edges") or []
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- ChemELLM DEXPI-style subset: NOT a certified DEXPI 2.0 document. -->",
        "<!-- Equipment / Nozzle / PipingNetworkSystem / Instrumentation are mapped from the diagram model. -->",
        '<DEXPI xmlns="http://www.dexpi.org/schemas/dexpi/2.0" Version="subset-0.1" Diagram="%s">'
        % html.escape(str(model.get("drawing_number") or "PID-001")),
        "  <Equipment>",
    ]
    for node in nodes:
        kind = node.get("kind")
        if kind in EQUIPMENT_KINDS | VALVE_KINDS:
            lines.append('    <Equipment id="%s" type="%s" label="%s"/>' % (
                html.escape(str(node.get("id"))), html.escape(str(kind)), html.escape(str(node.get("label") or ""))))
    lines.append("  </Equipment>")
    lines.append("  <Instrumentation>")
    for node in nodes:
        if node.get("kind") in INSTRUMENT_KINDS:
            lines.append('    <Instrument id="%s" label="%s"/>' % (
                html.escape(str(node.get("id"))), html.escape(str(node.get("label") or ""))))
    lines.append("  </Instrumentation>")
    lines.append("  <PipingNetworkSystem>")
    for edge in edges:
        if (edge.get("kind") or "process") != "process":
            continue
        lines.append('    <Connection from="%s" to="%s" lineNumber="%s"/>' % (
            html.escape(str(edge.get("source"))), html.escape(str(edge.get("target"))),
            html.escape(str(edge.get("line_no") or ""))))
    lines.append("  </PipingNetworkSystem>")
    lines.append("  <SignalConnections>")
    for edge in edges:
        if (edge.get("kind") or "process") == "signal":
            lines.append('    <Signal from="%s" to="%s"/>' % (
                html.escape(str(edge.get("source"))), html.escape(str(edge.get("target")))))
    lines.append("  </SignalConnections>")
    lines.append("</DEXPI>")
    return "\n".join(lines) + "\n"


def build_pid_files(model: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height, primitives = _pid_primitives(model)
    svg_path = out_dir / "pid.svg"
    pdf_path = out_dir / "pid.pdf"
    dxf_path = out_dir / "pid.dxf"
    json_path = out_dir / "pid.json"
    dexpi_path = out_dir / "pid.dexpi.xml"
    svg_path.write_text(_svg(width, height, primitives), encoding="utf-8")
    _pdf(width, height, primitives, pdf_path)
    _dxf(width, height, primitives, dxf_path)
    json_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    dexpi_path.write_text(_dexpi_subset(model), encoding="utf-8")
    validation = validate_pid(model)
    return {
        "svg": svg_path,
        "pdf": pdf_path,
        "dxf": dxf_path,
        "json": json_path,
        "dexpi": dexpi_path,
        "validation": validation,
    }


# --------------------------------------------------------------------------- #
# Parametric equipment CAD
# --------------------------------------------------------------------------- #
CAD_DEFAULTS = {
    "vessel": {"diameter_mm": 3000.0, "tangent_length_mm": 8000.0, "skirt_height_mm": 2000.0},
    "column": {"diameter_mm": 2400.0, "tangent_length_mm": 20000.0, "skirt_height_mm": 3000.0},
    "tank": {"diameter_mm": 8000.0, "tangent_length_mm": 9000.0, "skirt_height_mm": 0.0},
    "exchanger": {"diameter_mm": 1200.0, "tangent_length_mm": 5000.0, "skirt_height_mm": 0.0},
}


def _cad_params(args: dict) -> dict:
    equipment_type = str(args.get("equipment_type") or "vessel").lower()
    if equipment_type not in CAD_DEFAULTS:
        raise ValueError("equipment_type 必须是 vessel/column/tank/exchanger")
    defaults = CAD_DEFAULTS[equipment_type]
    diameter = float(args.get("diameter_mm", defaults["diameter_mm"]))
    length = float(args.get("tangent_length_mm", defaults["tangent_length_mm"]))
    skirt = float(args.get("skirt_height_mm", defaults["skirt_height_mm"]))
    head_height = float(args.get("head_height_mm", diameter / 4.0))
    if not (200.0 <= diameter <= 12000.0):
        raise ValueError("diameter_mm 超出 200~12000 mm 范围")
    if not (300.0 <= length <= 80000.0):
        raise ValueError("tangent_length_mm 超出 300~80000 mm 范围")
    if not (0.0 <= skirt <= 30000.0):
        raise ValueError("skirt_height_mm 超出 0~30000 mm 范围")
    if equipment_type in {"vessel", "column"} and not (diameter * 0.1 <= head_height <= diameter * 0.6):
        raise ValueError("head_height_mm 应在 0.1D~0.6D 之间")
    nozzles = args.get("nozzles") or []
    if not isinstance(nozzles, list):
        raise ValueError("nozzles 必须是数组")
    clean_nozzles = []
    for index, nozzle in enumerate(nozzles):
        dn = float(nozzle.get("dn", 100.0))
        if not (10.0 <= dn <= diameter * 0.5):
            raise ValueError(f"nozzle {index}: dn 超出 10~D/2 范围")
        position = str(nozzle.get("position") or "top").lower()
        if position not in {"top", "bottom", "side"}:
            raise ValueError(f"nozzle {index}: position 必须是 top/bottom/side")
        clean_nozzles.append({
            "tag": str(nozzle.get("tag") or f"N{index + 1}"),
            "dn": dn,
            "position": position,
            "offset_mm": float(nozzle.get("offset_mm", 0.0)),
            "azimuth_deg": float(nozzle.get("azimuth_deg", 0.0)),
            "elevation_mm": float(nozzle.get("elevation_mm", length / 2.0)),
        })
    return {
        "equipment_type": equipment_type,
        "tag": str(args.get("tag") or "EQ-001"),
        "diameter_mm": diameter,
        "tangent_length_mm": length,
        "skirt_height_mm": skirt,
        "head_height_mm": head_height,
        "nozzles": clean_nozzles,
    }


def _cad_script(params: dict, fcstd: Path, step: Path) -> str:
    return f'''
import math
import FreeCAD
import Part

P = {json.dumps(params, ensure_ascii=False)}
D = P["diameter_mm"]
L = P["tangent_length_mm"]
H = P["head_height_mm"]
SKIRT = P["skirt_height_mm"]
KIND = P["equipment_type"]
TAG = P["tag"]

def head_solid(radius, height, z, downward=False):
    ellipse = Part.Ellipse(FreeCAD.Vector(0, 0, 0), radius, height)
    arc = Part.ArcOfEllipse(ellipse, 0.0, math.pi / 2.0)
    l1 = Part.LineSegment(arc.EndPoint, FreeCAD.Vector(0, 0, 0))
    l2 = Part.LineSegment(FreeCAD.Vector(0, 0, 0), arc.StartPoint)
    face = Part.Face(Part.Wire([arc.toShape(), l1.toShape(), l2.toShape()]))
    face.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90.0)
    solid = face.revolve(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 360.0)
    if downward:
        solid.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 180.0)
    solid.translate(FreeCAD.Vector(0, 0, z))
    return solid

shapes = []
if KIND in ("vessel", "column"):
    shapes.append(Part.makeCylinder(D / 2.0, L, FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1)))
    shapes.append(head_solid(D / 2.0, H, L))
    shapes.append(head_solid(D / 2.0, H, 0.0, True))
    if SKIRT > 0:
        shapes.append(Part.makeCylinder(D / 2.0 + 100.0, SKIRT + 20.0,
                                        FreeCAD.Vector(0, 0, -SKIRT), FreeCAD.Vector(0, 0, 1)))
elif KIND == "tank":
    shapes.append(Part.makeCylinder(D / 2.0, L, FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1)))
    shapes.append(Part.makeCylinder(D / 2.0, 20.0, FreeCAD.Vector(0, 0, -20.0), FreeCAD.Vector(0, 0, 1)))
    shapes.append(Part.makeCone(D / 2.0, 8.0, max(200.0, D * 0.15),
                                FreeCAD.Vector(0, 0, L), FreeCAD.Vector(0, 0, 1)))
elif KIND == "exchanger":
    shapes.append(Part.makeCylinder(D / 2.0, L, FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0)))
    for x, sign in ((0.0, -1.0), (L, 1.0)):
        head = head_solid(D / 2.0, H, 0.0)
        head.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 1, 0), -90.0 if sign > 0 else 90.0)
        head.translate(FreeCAD.Vector(x, 0, 0))
        shapes.append(head)
    if SKIRT > 0:
        for x in (L * 0.25, L * 0.75):
            shapes.append(Part.makeBox(D * 0.6, D * 1.2, SKIRT,
                                       FreeCAD.Vector(x - D * 0.3, -D * 0.6, -SKIRT - D / 2.0)))

nozzle_shapes = []
for nozzle in P["nozzles"]:
    r = nozzle["dn"] / 2.0
    length = max(250.0, nozzle["dn"] * 2.0)
    if KIND == "exchanger":
        if nozzle["position"] == "top":
            nozzle_shapes.append(Part.makeCylinder(r, length, FreeCAD.Vector(L / 2.0, 0, D / 2.0 - 20.0),
                                                   FreeCAD.Vector(0, 0, 1)))
        else:
            nozzle_shapes.append(Part.makeCylinder(r, length, FreeCAD.Vector(L / 2.0, 0, -D / 2.0 + 20.0),
                                                   FreeCAD.Vector(0, 0, -1)))
        continue
    angle = math.radians(nozzle["azimuth_deg"])
    if nozzle["position"] == "top":
        nozzle_shapes.append(Part.makeCylinder(r, length, FreeCAD.Vector(nozzle["offset_mm"], 0, L + H - 30.0),
                                               FreeCAD.Vector(0, 0, 1)))
    elif nozzle["position"] == "bottom":
        nozzle_shapes.append(Part.makeCylinder(r, length, FreeCAD.Vector(nozzle["offset_mm"], 0, -H + 30.0),
                                               FreeCAD.Vector(0, 0, -1)))
    else:
        base = FreeCAD.Vector((D / 2.0 - 20.0) * math.cos(angle), (D / 2.0 - 20.0) * math.sin(angle),
                              nozzle["elevation_mm"])
        direction = FreeCAD.Vector(math.cos(angle), math.sin(angle), 0)
        nozzle_shapes.append(Part.makeCylinder(r, length, base, direction))

if nozzle_shapes:
    shapes.append(shapes[0].fuse(nozzle_shapes))
    shapes = shapes[1:]

shape = shapes[0]
if len(shapes) > 1:
    shape = shape.fuse(shapes[1:])
shape = shape.removeSplitter()

doc = FreeCAD.newDocument("ChemELLM_CAD")
obj = doc.addObject("Part::Feature", TAG)
obj.Shape = shape
doc.recompute()
doc.saveAs({fcstd.as_posix()!r})
Part.export([obj], {step.as_posix()!r})
'''


def _cad_preview_svg(params: dict) -> str:
    kind = params["equipment_type"]
    diameter = params["diameter_mm"]
    length = params["tangent_length_mm"]
    head = params["head_height_mm"]
    skirt = params["skirt_height_mm"]
    tag = params["tag"]
    nozzles = params["nozzles"]
    if kind == "exchanger":
        total_w = length + 2 * head
        total_h = diameter
        view_w, view_h = 1200, 420
        scale = min((view_w - 160) / total_w, (view_h - 120) / total_h)
        ox = (view_w - total_w * scale) / 2.0
        oy = (view_h - total_h * scale) / 2.0
        cx = ox + head * scale
        cy = oy + diameter * scale
        body = f'<rect x="{cx:.1f}" y="{oy:.1f}" width="{length * scale:.1f}" height="{diameter * scale:.1f}" fill="#eef4fb" stroke="#37506b" stroke-width="1.6"/>'
        left = f'<ellipse cx="{cx:.1f}" cy="{cy - diameter * scale / 2:.1f}" rx="{head * scale:.1f}" ry="{diameter * scale / 2:.1f}" fill="#eef4fb" stroke="#37506b" stroke-width="1.6"/>'
        right = f'<ellipse cx="{cx + length * scale:.1f}" cy="{cy - diameter * scale / 2:.1f}" rx="{head * scale:.1f}" ry="{diameter * scale / 2:.1f}" fill="#eef4fb" stroke="#37506b" stroke-width="1.6"/>'
        support = ""
        if skirt > 0:
            for fraction in (0.25, 0.75):
                sx = cx + length * scale * fraction
                support += f'<rect x="{sx - diameter * scale * 0.3:.1f}" y="{cy:.1f}" width="{diameter * scale * 0.6:.1f}" height="{skirt * scale:.1f}" fill="#d9e6f2" stroke="#37506b" stroke-width="1.0"/>'
        nozzle_svg = "".join(
            f'<rect x="{cx + length * scale / 2:.1f}" y="{cy - diameter * scale / 2 - 18:.1f}" width="10" height="18" fill="#cfe3f5" stroke="#37506b"/>'
            for _ in nozzles[:2]
        )
        drawing = body + left + right + support + nozzle_svg
        note = f"{tag} · 换热器 · 壳体 DN{diameter:.0f} × {length:.0f} mm（简化实体，不含管束/管板/法兰）"
    else:
        total_h = length + 2 * head + skirt
        total_w = diameter
        view_w, view_h = 1200, 760
        scale = min((view_w - 240) / total_w, (view_h - 180) / total_h)
        ox = (view_w - total_w * scale) / 2.0
        bottom = view_h - 80.0
        top = bottom - total_h * scale
        cx = ox + diameter * scale / 2.0
        shell_top = top + head * scale
        shell_bottom = shell_top + length * scale
        body = f'<rect x="{ox:.1f}" y="{shell_top:.1f}" width="{diameter * scale:.1f}" height="{length * scale:.1f}" fill="#eef4fb" stroke="#37506b" stroke-width="1.6"/>'
        top_head = f'<path d="M {ox:.1f} {shell_top:.1f} A {diameter * scale / 2:.1f} {head * scale:.1f} 0 0 1 {ox + diameter * scale:.1f} {shell_top:.1f}" fill="#eef4fb" stroke="#37506b" stroke-width="1.6"/>'
        bottom_head = f'<path d="M {ox:.1f} {shell_bottom:.1f} A {diameter * scale / 2:.1f} {head * scale:.1f} 0 0 0 {ox + diameter * scale:.1f} {shell_bottom:.1f}" fill="#eef4fb" stroke="#37506b" stroke-width="1.6"/>'
        skirt_svg = ""
        if skirt > 0:
            skirt_svg = f'<rect x="{ox + diameter * scale * 0.05:.1f}" y="{shell_bottom + head * scale:.1f}" width="{diameter * scale * 0.9:.1f}" height="{skirt * scale:.1f}" fill="#d9e6f2" stroke="#37506b" stroke-width="1.0"/>'
        nozzle_svg = ""
        for nozzle in nozzles:
            if nozzle["position"] == "top":
                nx = cx + nozzle["offset_mm"] * scale
                nozzle_svg += f'<rect x="{nx - 5:.1f}" y="{top - 16:.1f}" width="10" height="18" fill="#cfe3f5" stroke="#37506b"/>'
            elif nozzle["position"] == "side":
                ny = shell_top + nozzle["elevation_mm"] * scale
                nozzle_svg += f'<rect x="{ox + diameter * scale - 4:.1f}" y="{ny - 5:.1f}" width="16" height="10" fill="#cfe3f5" stroke="#37506b"/>'
        drawing = body + top_head + bottom_head + skirt_svg + nozzle_svg
        note = f"{tag} · {'塔器' if kind == 'column' else ('储罐' if kind == 'tank' else '容器')} · DN{diameter:.0f} × {length:.0f} mm 切线（简化实体，不含壁厚/开孔补强/内件）"

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{view_w}" height="{view_h}" viewBox="0 0 {view_w} {view_h}">\n'
        f'<rect width="{view_w}" height="{view_h}" fill="#ffffff"/>\n'
        f'<text x="24" y="34" font-size="18" fill="#1b2a3a">{html.escape(note)}</text>\n'
        f'<text x="24" y="56" font-size="12" fill="#5a6b7d">立面示意（单位 mm）；尺寸为参数化输入，未做强度、开孔补强与制造校核。</text>\n'
        f"{drawing}\n"
        "</svg>\n"
    )


def build_cad_files(args: dict, out_dir: Path, freecad_executable: Path) -> dict:
    params = _cad_params(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "part.py"
    fcstd = out_dir / "part.FCStd"
    step = out_dir / "part.step"
    preview = out_dir / "part-preview.svg"
    script.write_text(_cad_script(params, fcstd, step), encoding="utf-8")
    completed = subprocess.run([str(freecad_executable), str(script)], capture_output=True, timeout=300)
    if completed.returncode != 0:
        raise RuntimeError("FreeCAD 建模失败：" + completed.stderr.decode("utf-8", "replace")[-800:])
    if not fcstd.is_file() or not step.is_file():
        raise RuntimeError("FreeCAD 未生成 FCStd/STEP")
    preview.write_text(_cad_preview_svg(params), encoding="utf-8")
    return {"params": params, "script": script, "fcstd": fcstd, "step": step, "preview": preview}
