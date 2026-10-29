"""Engineering-style P&ID sheet renderer.

The drawing is a single A1 landscape sheet in millimetres with:
- GB/T 14689 style frame, zone grid, title block, revision block;
- ISA-5.1 style instrument bubbles, control valves, equipment symbols;
- orthogonal routed process lines, dashed signal lines, dash-dot interlocks;
- equipment list, instrument/interlock list, line list, legend and notes.

Everything is deterministic: the structured model in, vector drawing out.
"""

from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path

SHEET_W, SHEET_H = 841.0, 594.0
LEFT_MARGIN, MARGIN = 25.0, 10.0
FRAME = (LEFT_MARGIN, MARGIN, SHEET_W - MARGIN, SHEET_H - MARGIN)

DRAW_AREA = (35.0, 160.0, 600.0, 565.0)
LINE_TABLE = (35.0, 60.0, 600.0, 150.0)
TITLE_BLOCK = (651.0, 524.0, 827.0, 580.0)
REVISION_BLOCK = (651.0, 496.0, 827.0, 524.0)
EQUIP_TABLE = (610.0, 312.0, 827.0, 415.0)
INST_TABLE = (610.0, 215.0, 827.0, 308.0)
LEGEND_AREA = (610.0, 422.0, 827.0, 495.0)
NOTES_AREA = (610.0, 100.0, 827.0, 210.0)

EQUIPMENT_KINDS = {"tank", "column", "reactor", "exchanger", "pump", "vessel"}
INSTRUMENT_KINDS = {"instrument"}
VALVE_KINDS = {"control_valve"}
DRAWING_KINDS = EQUIPMENT_KINDS | INSTRUMENT_KINDS | VALVE_KINDS
LINE_NO_RE = re.compile(r"^[A-Z]{1,6}-\d{2,4}-\d{1,3}-[A-Z0-9]{1,6}$")
INSTRUMENT_TAG_RE = re.compile(r"^(TI|TIC|PI|PIC|FIC|FIQ|LI|LIC|AI|AIC|XI|XIC|FI|PDI)[A-Z]?-?\d+[A-Z]?$")
VALVE_TAG_RE = re.compile(r"^(FV|LV|TV|PV|XV|HV)-?\d+[A-Z]?$")

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
    "frame": "#1f3b57",
    "grid": "#b0bec5",
}


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
    for key in ("title", "drawing_number"):
        if not str(model.get(key) or "").strip():
            warnings.append(f"缺少 {key}，标题栏将使用默认值")

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
            warnings.append(f"{node_id}：仪表位号不符合 TI/TIC/PI/FIC 等规范")
        if kind in VALVE_KINDS and not VALVE_TAG_RE.match(node_id):
            warnings.append(f"{node_id}：控制阀位号不符合 FV/LV/TV/PV 等规范")
        if kind in EQUIPMENT_KINDS and not str(node.get("spec") or "").strip():
            warnings.append(f"{node_id}：设备未填 spec（规格/主要参数），设备表将显示“— ”")

    line_numbers: dict[str, int] = {}
    signal_degree = {node_id: 0 for node_id in ids}
    process_degree = {node_id: 0 for node_id in ids}
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
            process_degree[source] += 1
            process_degree[target] += 1
            line_no = str(edge.get("line_no") or "").strip()
            if not line_no:
                warnings.append(f"边 {index}（{source}→{target}）：缺少管线号 line_no")
            else:
                if not LINE_NO_RE.match(line_no):
                    warnings.append(f"边 {index}：管线号格式不像 PG-0101-100-A1A：{line_no}")
                if line_no in line_numbers:
                    blockers.append(f"管线号重复：{line_no}（边 {line_numbers[line_no]} 与 {index}）")
                else:
                    line_numbers[line_no] = index
        elif kind in {"signal", "interlock"}:
            signal_degree[source] += 1
            signal_degree[target] += 1
            if source_kind not in INSTRUMENT_KINDS:
                warnings.append(f"边 {index}：{kind} 线 source {source} 不是仪表")
            if target_kind not in (INSTRUMENT_KINDS | VALVE_KINDS | EQUIPMENT_KINDS):
                blockers.append(f"边 {index}：{kind} 线 target 类型不支持：{target_kind}")
        else:
            blockers.append(f"边 {index}：不支持的 kind={kind}")

    for node_id, node in ids.items():
        kind = node.get("kind")
        if kind in VALVE_KINDS:
            if signal_degree[node_id] == 0:
                blockers.append(f"{node_id}：控制阀没有信号线（需要仪表→阀门）")
            if process_degree[node_id] == 0:
                blockers.append(f"{node_id}：控制阀没有工艺管线")
        if kind in INSTRUMENT_KINDS and (signal_degree[node_id] + process_degree[node_id]) == 0:
            blockers.append(f"{node_id}：仪表没有连接任何线")
        if kind in EQUIPMENT_KINDS and process_degree[node_id] == 0:
            blockers.append(f"{node_id}：设备没有工艺管线连接")

    instruments = [node_id for node_id, node in ids.items() if node.get("kind") in INSTRUMENT_KINDS]
    valves = [node_id for node_id, node in ids.items() if node.get("kind") in VALVE_KINDS]
    if not instruments:
        warnings.append("图上没有仪表；若要表达 P&ID，建议补充 TI/PI/FIC 等")
    if instruments and not valves:
        warnings.append("有仪表但没有控制阀；确认是否需要调节回路")
    # control loop completeness: controller (C in function) should reach a valve
    valve_targets = {
        str(edge.get("source")) for edge in edges
        if (edge.get("kind") or "process") == "signal" and str(edge.get("target")) in valves
    }
    for instrument in instruments:
        if "C" in instrument.split("-")[0] and instrument not in valve_targets:
            warnings.append(f"{instrument}：控制器未连接到任何控制阀")

    return {
        "passed": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "counts": {"nodes": len(ids), "edges": len(edges), "instruments": len(instruments), "valves": len(valves)},
    }


# --------------------------------------------------------------------------- #
# Symbols (all sizes in mm)
# --------------------------------------------------------------------------- #
def _equipment_box(kind: str) -> tuple[float, float]:
    if kind == "column":
        return 16.0, 58.0
    if kind == "reactor" or kind == "vessel":
        return 22.0, 34.0
    if kind == "tank":
        return 22.0, 26.0
    if kind == "exchanger":
        return 20.0, 20.0
    if kind == "pump":
        return 14.0, 14.0
    return 20.0, 20.0


def _symbol(node: dict, x: float, y: float) -> list[tuple]:
    kind = node.get("kind")
    tag = str(node.get("id") or "")
    label = str(node.get("label") or "")
    ink = PALETTE["equipment_stroke"]
    fill = PALETTE["equipment"]
    out: list[tuple] = []
    if kind == "column":
        w, h = 16.0, 58.0
        out.append(("rect", x - w / 2, y - h / 2, w, h, fill, ink, 0.5, 2.0))
        for step in range(1, 9):
            yy = y - h / 2 + h * step / 9
            out.append(("line", x - w / 2 + 1.5, yy, x + w / 2 - 1.5, yy, ink, 0.25, None))
        out.append(("line", x, y + h / 2, x, y + h / 2 + 4, ink, 0.5, None))
        out.append(("text", x, y - h / 2 - 2.0, tag, 2.8, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + h / 2 + 7.0, label, 2.4, PALETTE["muted"], "middle", False))
    elif kind in {"reactor", "vessel"}:
        w, h = 22.0, 34.0
        out.append(("rect", x - w / 2, y - h / 2, w, h, fill, ink, 0.5, 3.0))
        out.append(("line", x, y - h / 2 + 2, x, y + h / 2 - 2, ink, 0.25, None))
        out.append(("line", x - w / 2 + 2, y + 3, x + w / 2 - 2, y + 3, ink, 0.25, None))
        out.append(("text", x, y - h / 2 - 2.0, tag, 2.8, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + h / 2 + 6.0, label, 2.4, PALETTE["muted"], "middle", False))
    elif kind == "tank":
        w, h = 22.0, 26.0
        out.append(("rect", x - w / 2, y - h / 2, w, h, fill, ink, 0.5, 1.5))
        out.append(("ellipse", x, y - h / 2, w / 2, 2.5, fill, ink, 0.4))
        out.append(("text", x, y - h / 2 - 2.0, tag, 2.8, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + h / 2 + 6.0, label, 2.4, PALETTE["muted"], "middle", False))
    elif kind == "exchanger":
        r = 9.0
        out.append(("circle", x, y, r, fill, ink, 0.5))
        out.append(("line", x - r + 2, y + 3.5, x + r - 2, y + 3.5, ink, 0.25, None))
        out.append(("line", x - r + 2, y - 3.5, x + r - 2, y - 3.5, ink, 0.25, None))
        out.append(("line", x - r + 2, y + 3.5, x + r - 2, y - 3.5, ink, 0.25, None))
        out.append(("text", x, y - r - 3.0, tag, 2.8, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + r + 6.0, label, 2.4, PALETTE["muted"], "middle", False))
    elif kind == "pump":
        r = 7.0
        out.append(("circle", x, y, r, fill, ink, 0.5))
        out.append(("polygon", [(x - 3.5, y - 4.5), (x + 4.5, y), (x - 3.5, y + 4.5)], fill, ink, 0.4))
        out.append(("text", x, y - r - 3.0, tag, 3.4, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + r + 7.0, label, 2.7, PALETTE["muted"], "middle", False))
    elif kind == "instrument":
        r = 7.0
        out.append(("circle", x, y, r, PALETTE["instrument"], ink, 0.5))
        out.append(("line", x - r, y, x + r, y, ink, 0.25, None))
        match = re.match(r"^([A-Z]+)-?(\d+[A-Z]?)$", tag)
        if match:
            out.append(("text", x, y - 0.8, match.group(1), 2.8, PALETTE["ink"], "middle", True))
            out.append(("text", x, y + 3.4, match.group(2), 2.4, PALETTE["ink"], "middle", False))
        else:
            out.append(("text", x, y + 0.8, tag, 2.8, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + r + 4.5, label, 2.5, PALETTE["muted"], "middle", False))
    elif kind == "control_valve":
        w, h = 7.0, 4.0
        out.append(("polygon", [(x - w, y - h), (x, y), (x - w, y + h)], fill, ink, 0.45))
        out.append(("polygon", [(x + w, y - h), (x, y), (x + w, y + h)], fill, ink, 0.45))
        out.append(("line", x, y - h, x, y - 8.0, ink, 0.4, None))
        out.append(("ellipse", x, y - 9.5, 4.0, 1.6, PALETTE["instrument"], ink, 0.4))
        out.append(("text", x, y - 13.0, tag, 3.0, PALETTE["ink"], "middle", True))
        out.append(("text", x, y + 6.0, label, 2.4, PALETTE["muted"], "middle", False))
    return out


def _segment_hits_box(p1, p2, box) -> bool:
    x0, y0, x1, y1 = box
    ax, ay = p1
    bx, by = p2
    if abs(ax - bx) < 1e-6:  # vertical
        return x0 <= ax <= x1 and not (max(ay, by) < y0 or min(ay, by) > y1)
    if abs(ay - by) < 1e-6:  # horizontal
        return y0 <= ay <= y1 and not (max(ax, bx) < x0 or min(ax, bx) > x1)
    return True


def _route(p1, p2, obstacles):
    candidates = [
        [p1, (p2[0], p1[1]), p2],
        [p1, (p1[0], p2[1]), p2],
        [p1, ((p1[0] + p2[0]) / 2, p1[1]), ((p1[0] + p2[0]) / 2, p2[1]), p2],
        [p1, (p1[0], (p1[1] + p2[1]) / 2), (p2[0], (p1[1] + p2[1]) / 2), p2],
    ]
    for candidate in candidates:
        ok = True
        for a, b in zip(candidate, candidate[1:]):
            for box in obstacles:
                if _segment_hits_box(a, b, box):
                    ok = False
                    break
            if not ok:
                break
        if ok:
            return candidate
    return candidates[0]


def _arrow(x1, y1, x2, y2, color) -> list[tuple]:
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 2.5
    left = (x2 - size * math.cos(angle - 0.45), y2 - size * math.sin(angle - 0.45))
    right = (x2 - size * math.cos(angle + 0.45), y2 - size * math.sin(angle + 0.45))
    return [("polygon", [left, right, (x2, y2)], color, color, 0.2)]


def _text_width(text: str, size: float) -> float:
    ascii_len = sum(1 for ch in text if ord(ch) < 128)
    cjk_len = len(text) - ascii_len
    return ascii_len * size * 0.55 + cjk_len * size


def _table(prims: list[tuple], area, title: str, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
    x0, y0, x1, y1 = area
    prims.append(("text", x0, y0 - 2.5, title, 3.4, PALETTE["ink"], "start", True))
    row_h = min(8.0, (y1 - y0 - 4.0) / max(1, len(rows) + 1))
    widths = [w * (x1 - x0) / sum(widths) for w in widths]
    y = y0
    prims.append(("rect", x0, y, x1 - x0, row_h, "#dce8f4", PALETTE["equipment_stroke"], 0.4, 0.0))
    cx = x0
    for header, width in zip(headers, widths):
        prims.append(("text", cx + 1.2, y + row_h - 2.2, header, 2.7, PALETTE["ink"], "start", True))
        cx += width
    y += row_h
    for row in rows:
        prims.append(("rect", x0, y, x1 - x0, row_h, None, PALETTE["grid"], 0.3, 0.0))
        cx = x0
        for value, width in zip(row, widths):
            text = str(value)
            while _text_width(text, 2.5) > width - 2.4 and len(text) > 2:
                text = text[:-1]
            prims.append(("text", cx + 1.2, y + row_h - 2.2, text, 2.5, PALETTE["ink"], "start", False))
            cx += width
        y += row_h
    # cell borders
    prims.append(("rect", x0, y0, x1 - x0, y - y0, None, PALETTE["equipment_stroke"], 0.4, 0.0))
    cx = x0
    for width in widths[:-1]:
        cx += width
        prims.append(("line", cx, y0, cx, y, PALETTE["grid"], 0.3, None))
    yy = y0 + row_h
    while yy < y - 0.1:
        prims.append(("line", x0, yy, x1, yy, PALETTE["grid"], 0.3, None))
        yy += row_h


def _sheet_primitives(model: dict) -> tuple[float, float, list[tuple]]:
    W, H = SHEET_W, SHEET_H
    prims: list[tuple] = [
        ("rect", 0, 0, W, H, PALETTE["paper"], None, 0.0, 0.0),
        ("rect", FRAME[0], FRAME[1], FRAME[2] - FRAME[0], FRAME[3] - FRAME[1], None, PALETTE["frame"], 0.7, 0.0),
        ("rect", FRAME[0] + 4, FRAME[1] + 4, FRAME[2] - FRAME[0] - 8, FRAME[3] - FRAME[1] - 8, None, PALETTE["frame"], 0.35, 0.0),
    ]
    # zone grid
    inner = (FRAME[0] + 4, FRAME[1] + 4, FRAME[2] - 4, FRAME[3] - 4)
    cols, rows = 8, 6
    for index in range(cols + 1):
        x = inner[0] + (inner[2] - inner[0]) * index / cols
        prims.append(("line", x, inner[1], x, inner[1] + 3, PALETTE["frame"], 0.35, None))
        prims.append(("line", x, inner[3] - 3, x, inner[3], PALETTE["frame"], 0.35, None))
    for index in range(cols):
        x = inner[0] + (inner[2] - inner[0]) * (index + 0.5) / cols
        prims.append(("text", x, inner[1] + 2.4, str(index + 1), 2.4, PALETTE["muted"], "middle", False))
        prims.append(("text", x, inner[3] - 0.8, str(index + 1), 2.4, PALETTE["muted"], "middle", False))
    for index in range(rows + 1):
        y = inner[1] + (inner[3] - inner[1]) * index / rows
        prims.append(("line", inner[0], y, inner[0] + 3, y, PALETTE["frame"], 0.35, None))
        prims.append(("line", inner[2] - 3, y, inner[2], y, PALETTE["frame"], 0.35, None))
    for index in range(rows):
        y = inner[1] + (inner[3] - inner[1]) * (index + 0.5) / rows
        letter = chr(ord("A") + index)
        prims.append(("text", inner[0] + 1.5, y, letter, 2.4, PALETTE["muted"], "middle", False))
        prims.append(("text", inner[2] - 1.5, y, letter, 2.4, PALETTE["muted"], "middle", False))

    # title block
    tx0, ty0, tx1, ty1 = TITLE_BLOCK
    prims.append(("rect", tx0, ty0, tx1 - tx0, ty1 - ty0, None, PALETTE["frame"], 0.7, 0.0))
    for offset in (22.0, 34.0, 44.0):
        prims.append(("line", tx0, ty0 + offset, tx1, ty0 + offset, PALETTE["frame"], 0.35, None))
    prims.append(("line", tx0 + 88, ty0 + 22, tx0 + 88, ty1, PALETTE["frame"], 0.35, None))
    title = str(model.get("title") or "工艺管道及仪表流程图（P&ID）")
    drawing_no = str(model.get("drawing_number") or "PID-001")
    revision = str(model.get("revision") or "A")
    prims.append(("text", tx0 + 3, ty0 + 8, title, 4.2, PALETTE["ink"], "start", True))
    prims.append(("text", tx0 + 3, ty0 + 17, f"图号 {drawing_no}    版本 {revision}", 2.5, PALETTE["muted"], "start", False))
    prims.append(("text", tx0 + 3, ty0 + 28, f"设计 {model.get('designer') or '—'}", 2.4, PALETTE["ink"], "start", False))
    prims.append(("text", tx0 + 3, ty0 + 40, f"校核 {model.get('checker') or '—'}", 2.4, PALETTE["ink"], "start", False))
    prims.append(("text", tx0 + 91, ty0 + 28, f"审核 {model.get('approver') or '—'}", 2.4, PALETTE["ink"], "start", False))
    prims.append(("text", tx0 + 91, ty0 + 40, "比例 不按比例（示意）    单位 mm", 2.4, PALETTE["ink"], "start", False))
    prims.append(("text", tx0 + 3, ty0 + 50, str(model.get("owner") or "ChemELLM 本地化工工作台"), 2.3, PALETTE["muted"], "start", False))
    prims.append(("text", tx0 + 91, ty0 + 50, f"日期 {model.get('date') or '—'}    共 1 张 第 1 张", 2.2, PALETTE["muted"], "start", False))

    # revision block
    rx0, ry0, rx1, ry1 = REVISION_BLOCK
    prims.append(("rect", rx0, ry0, rx1 - rx0, ry1 - ry0, None, PALETTE["frame"], 0.5, 0.0))
    prims.append(("text", rx0 + 3, ry0 + 5.5, "版本", 2.2, PALETTE["muted"], "start", True))
    prims.append(("text", rx0 + 30, ry0 + 5.5, "日期", 2.2, PALETTE["muted"], "start", True))
    prims.append(("text", rx0 + 70, ry0 + 5.5, "修改说明", 2.2, PALETTE["muted"], "start", True))
    prims.append(("text", rx0 + 3, ry0 + 14, revision, 2.4, PALETTE["ink"], "start", False))
    prims.append(("text", rx0 + 30, ry0 + 14, str(model.get("date") or "—"), 2.4, PALETTE["ink"], "start", False))
    prims.append(("text", rx0 + 70, ry0 + 14, str(model.get("revision_note") or "初版"), 2.4, PALETTE["ink"], "start", False))

    # drawing area
    area = DRAW_AREA
    prims.append(("rect", area[0], area[1], area[2] - area[0], area[3] - area[1], None, PALETTE["grid"], 0.25, 0.0))
    nodes = [node for node in (model.get("nodes") or []) if node.get("id")]
    xs = [float(node.get("x", 0.0)) for node in nodes] or [0.0]
    ys = [float(node.get("y", 0.0)) for node in nodes] or [0.0]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    span_x = max(maxx - minx, 1e-6)
    span_y = max(maxy - miny, 1e-6)
    pad = 24.0
    scale = min((area[2] - area[0] - 2 * pad) / span_x, (area[3] - area[1] - 2 * pad) / span_y)
    ox = area[0] + pad + ((area[2] - area[0] - 2 * pad) - span_x * scale) / 2.0
    oy = area[1] + pad + ((area[3] - area[1] - 2 * pad) - span_y * scale) / 2.0

    def screen(node):
        return (ox + (float(node.get("x", 0.0)) - minx) * scale,
                oy + (float(node.get("y", 0.0)) - miny) * scale)

    positions = {str(node["id"]): screen(node) for node in nodes}
    boxes = []
    for node in nodes:
        kind = node.get("kind")
        if kind in EQUIPMENT_KINDS:
            x, y = positions[str(node["id"])]
            w, h = _equipment_box(kind)
            boxes.append((x - w / 2 - 2.0, y - h / 2 - 2.0, x + w / 2 + 2.0, y + h / 2 + 2.0))

    for node in nodes:
        x, y = positions[str(node["id"])]
        prims.extend(_symbol(node, x, y))

    for edge in model.get("edges") or []:
        source = positions.get(str(edge.get("source")))
        target = positions.get(str(edge.get("target")))
        if not source or not target:
            continue
        kind = edge.get("kind") or "process"
        if kind == "process":
            obstacles = [box for box in boxes]
            points = _route(source, target, obstacles)
            for a, b in zip(points, points[1:]):
                prims.append(("line", a[0], a[1], b[0], b[1], PALETTE["process"], 0.5, None))
            prims.extend(_arrow(points[-2][0], points[-2][1], points[-1][0], points[-1][1], PALETTE["process"]))
            label = str(edge.get("line_no") or edge.get("label") or "")
            if label:
                best = max(zip(points, points[1:]), key=lambda pair: abs(pair[0][0] - pair[1][0]) + abs(pair[0][1] - pair[1][1]))
                mx = (best[0][0] + best[1][0]) / 2.0
                my = (best[0][1] + best[1][1]) / 2.0 - 2.0
                width = _text_width(label, 2.2) + 2.0
                prims.append(("rect", mx - width / 2, my - 2.6, width, 3.4, PALETTE["paper"], None, 0.0, 0.0))
                prims.append(("text", mx, my, label, 2.2, PALETTE["muted"], "middle", False))
        else:
            color = PALETTE["signal"] if kind == "signal" else PALETTE["interlock"]
            dash = "dash" if kind == "signal" else "dashdot"
            width = 0.25 if kind == "signal" else 0.35
            prims.append(("line", source[0], source[1], target[0], target[1], color, width, dash))
            label = str(edge.get("label") or "")
            if label:
                mx = (source[0] + target[0]) / 2.0
                my = (source[1] + target[1]) / 2.0
                prims.append(("text", mx, my - 1.5, label, 2.2, color, "middle", False))

    # equipment / instrument / line tables
    equipment_rows = [
        [str(node.get("id")), str(node.get("label") or ""), str(node.get("spec") or "—"),
         str(node.get("material") or "—"), "1"]
        for node in nodes if node.get("kind") in EQUIPMENT_KINDS
    ]
    _table(prims, EQUIP_TABLE, "设备一览表", ["位号", "名称", "规格/参数", "材料", "数量"], equipment_rows, [22, 28, 34, 16, 10])
    instrument_rows = [
        [str(node.get("id")), str(node.get("label") or ""), str(node.get("type") or "—"),
         str(node.get("interlock") or "—")]
        for node in nodes if node.get("kind") in INSTRUMENT_KINDS | VALVE_KINDS
    ]
    _table(prims, INST_TABLE, "仪表与联锁", ["位号", "功能/说明", "类型", "联锁动作"], instrument_rows, [24, 30, 22, 30])
    line_rows = [
        [str(edge.get("line_no") or "—"), str(edge.get("source")), str(edge.get("target")),
         str(edge.get("medium") or edge.get("label") or "—"), str(edge.get("spec") or "—")]
        for edge in (model.get("edges") or []) if (edge.get("kind") or "process") == "process"
    ][:8]
    _table(prims, LINE_TABLE, "管线表", ["管线号", "起点", "终点", "介质", "规格"], line_rows, [30, 22, 22, 26, 22])
    process_total = sum(1 for edge in (model.get("edges") or []) if (edge.get("kind") or "process") == "process")
    if process_total > len(line_rows):
        prims.append(("text", LINE_TABLE[0], LINE_TABLE[3] + 3.5,
                      f"（共 {process_total} 条工艺管线，本表显示前 {len(line_rows)} 条）", 2.2, PALETTE["muted"], "start", False))

    # legend (two columns)
    lx, ly, lrx, lry = LEGEND_AREA
    prims.append(("text", lx, ly - 2.0, "图例", 3.0, PALETTE["ink"], "start", True))
    legend = [
        ("仪器（现场）", "instrument"),
        ("控制阀", "valve"),
        ("工艺管线", "process"),
        ("仪表信号", "signal"),
        ("联锁", "interlock"),
    ]
    for index, (label, kind) in enumerate(legend):
        col = index % 2
        row = index // 2
        sx = lx + 8 + col * 100
        sy = ly + 8 + row * 16
        if kind == "instrument":
            prims.append(("circle", sx, sy, 4.0, PALETTE["instrument"], PALETTE["equipment_stroke"], 0.4))
        elif kind == "valve":
            prims.append(("polygon", [(sx - 4, sy - 2.5), (sx, sy), (sx - 4, sy + 2.5)], PALETTE["equipment"], PALETTE["equipment_stroke"], 0.4))
            prims.append(("polygon", [(sx + 4, sy - 2.5), (sx, sy), (sx + 4, sy + 2.5)], PALETTE["equipment"], PALETTE["equipment_stroke"], 0.4))
        elif kind == "process":
            prims.append(("line", sx - 7, sy, sx + 7, sy, PALETTE["process"], 0.5, None))
        elif kind == "signal":
            prims.append(("line", sx - 7, sy, sx + 7, sy, PALETTE["signal"], 0.3, "dash"))
        else:
            prims.append(("line", sx - 7, sy, sx + 7, sy, PALETTE["interlock"], 0.35, "dashdot"))
        prims.append(("text", sx + 13, sy + 1.2, label, 2.3, PALETTE["ink"], "start", False))

    # notes
    nx, ny, nrx, nry = NOTES_AREA
    prims.append(("text", nx, ny - 2.0, "说明", 3.0, PALETTE["ink"], "start", True))
    notes = [
        "1. 本图为规则生成的示意 P&ID，不按比例；未按 HG/T 20519 逐条对标，不能直接作为施工图。",
        "2. 尺寸单位 mm；管线号、位号与仪表功能按项目模型生成，需设计与校核确认。",
        "3. 仪表符号参考 ISA-5.1 简化画法；联锁线仅表示逻辑关系，不含电气接线。",
        "4. 设备规格/材料来自模型输入；空白处以“—”表示，待补充后方可用于工程。",
    ]
    for index, note in enumerate(notes):
        prims.append(("text", nx, ny + 6 + index * 8, note, 2.2, PALETTE["muted"], "start", False))

    return W, H, prims


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def _svg(W: float, H: float, prims: list[tuple]) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
    ]
    for primitive in prims:
        kind = primitive[0]
        if kind == "line":
            _, x1, y1, x2, y2, color, width, dash = primitive
            dash_attr = ""
            if dash == "dash":
                dash_attr = ' stroke-dasharray="3,2"'
            elif dash == "dashdot":
                dash_attr = ' stroke-dasharray="5,1.5,1,1.5"'
            parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="{width:.2f}"{dash_attr}/>')
        elif kind == "rect":
            _, x, y, w, h, fill, stroke, width, radius = primitive
            parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{radius:.2f}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width:.2f}"/>')
        elif kind == "circle":
            _, cx, cy, radius, fill, stroke, width = primitive
            parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width:.2f}"/>')
        elif kind == "ellipse":
            _, cx, cy, rx, ry, fill, stroke, width = primitive
            parts.append(f'<ellipse cx="{cx:.2f}" cy="{cy:.2f}" rx="{rx:.2f}" ry="{ry:.2f}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width:.2f}"/>')
        elif kind == "polygon":
            _, points, fill, stroke, width = primitive
            data = " ".join(f"{px:.2f},{py:.2f}" for px, py in points)
            parts.append(f'<polygon points="{data}" fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{width:.2f}"/>')
        elif kind == "text":
            _, x, y, text, size, color, anchor, bold = primitive
            weight = "bold" if bold else "normal"
            parts.append(f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size:.2f}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}" font-family="Arial, Noto Sans CJK SC, SimHei, sans-serif">{html.escape(str(text))}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _pdf(W: float, H: float, prims: list[tuple], path: Path) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import mm as MM
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as pdf_canvas

    font_name = "Helvetica"
    bold_name = "Helvetica-Bold"
    for candidate in (Path(r"C:\Windows\Fonts\simhei.ttf"), Path(r"C:\Windows\Fonts\Deng.ttf"), Path(r"C:\Windows\Fonts\msyh.ttc")):
        if not candidate.is_file():
            continue
        try:
            if "ChemCJK" not in pdfmetrics.getRegisteredFontNames():
                font = TTFont("ChemCJK", str(candidate), subfontIndex=0) if candidate.suffix.lower() == ".ttc" else TTFont("ChemCJK", str(candidate))
                pdfmetrics.registerFont(font)
            font_name = bold_name = "ChemCJK"
            break
        except Exception:
            continue

    pdf = pdf_canvas.Canvas(str(path), pagesize=(W * MM, H * MM))
    for primitive in prims:
        kind = primitive[0]
        if kind == "line":
            _, x1, y1, x2, y2, color, width, dash = primitive
            pdf.setStrokeColor(HexColor(color))
            pdf.setLineWidth(width * MM)
            if dash == "dash":
                pdf.setDash([3 * MM, 2 * MM], 0)
            elif dash == "dashdot":
                pdf.setDash([5 * MM, 1.5 * MM, 1 * MM, 1.5 * MM], 0)
            else:
                pdf.setDash()
            pdf.line(x1 * MM, (H - y1) * MM, x2 * MM, (H - y2) * MM)
        elif kind == "rect":
            _, x, y, w, h, fill, stroke, width, radius = primitive
            pdf.setDash()
            if fill:
                pdf.setFillColor(HexColor(fill))
            if stroke:
                pdf.setStrokeColor(HexColor(stroke))
                pdf.setLineWidth(max(width, 0.05) * MM)
            if radius:
                pdf.roundRect(x * MM, (H - y - h) * MM, w * MM, h * MM, radius * MM, stroke=1 if stroke else 0, fill=1 if fill else 0)
            else:
                pdf.rect(x * MM, (H - y - h) * MM, w * MM, h * MM, stroke=1 if stroke else 0, fill=1 if fill else 0)
        elif kind in {"circle", "ellipse"}:
            if kind == "circle":
                _, cx, cy, radius, fill, stroke, width = primitive
                rx = ry = radius
            else:
                _, cx, cy, rx, ry, fill, stroke, width = primitive
            pdf.setDash()
            if fill:
                pdf.setFillColor(HexColor(fill))
            if stroke:
                pdf.setStrokeColor(HexColor(stroke))
                pdf.setLineWidth(max(width, 0.05) * MM)
            pdf.ellipse((cx - rx) * MM, (H - cy - ry) * MM, (cx + rx) * MM, (H - cy + ry) * MM, stroke=1 if stroke else 0, fill=1 if fill else 0)
        elif kind == "polygon":
            _, points, fill, stroke, width = primitive
            path_obj = pdf.beginPath()
            path_obj.moveTo(points[0][0] * MM, (H - points[0][1]) * MM)
            for px, py in points[1:]:
                path_obj.lineTo(px * MM, (H - py) * MM)
            path_obj.close()
            pdf.setDash()
            if fill:
                pdf.setFillColor(HexColor(fill))
            if stroke:
                pdf.setStrokeColor(HexColor(stroke))
                pdf.setLineWidth(max(width, 0.05) * MM)
            pdf.drawPath(path_obj, stroke=1 if stroke else 0, fill=1 if fill else 0)
        elif kind == "text":
            _, x, y, text, size, color, anchor, bold = primitive
            pdf.setFillColor(HexColor(color))
            pdf.setFont(bold_name if bold else font_name, max(size, 0.8) * MM)
            if anchor == "middle":
                pdf.drawCentredString(x * MM, (H - y) * MM, str(text))
            elif anchor == "end":
                pdf.drawRightString(x * MM, (H - y) * MM, str(text))
            else:
                pdf.drawString(x * MM, (H - y) * MM, str(text))
    pdf.showPage()
    pdf.save()


def _dxf(W: float, H: float, prims: list[tuple], path: Path) -> None:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()
    for layer, color in (("FRAME", 7), ("EQUIPMENT", 5), ("PROCESS", 7), ("SIGNAL", 3), ("INTERLOCK", 1), ("TEXT", 7), ("TABLE", 8)):
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)
    for primitive in prims:
        kind = primitive[0]
        if kind == "line":
            _, x1, y1, x2, y2, color, width, dash = primitive
            layer = "SIGNAL" if color == PALETTE["signal"] else ("INTERLOCK" if color == PALETTE["interlock"] else ("PROCESS" if color == PALETTE["process"] else "FRAME"))
            msp.add_line((x1, H - y1), (x2, H - y2), dxfattribs={"layer": layer})
        elif kind == "rect":
            _, x, y, w, h, fill, stroke, width, radius = primitive
            layer = "FRAME" if stroke == PALETTE["frame"] else ("TABLE" if stroke == PALETTE["grid"] else "EQUIPMENT")
            msp.add_lwpolyline([(x, H - y), (x + w, H - y), (x + w, H - y - h), (x, H - y - h)], close=True, dxfattribs={"layer": layer})
        elif kind == "circle":
            _, cx, cy, radius, fill, stroke, width = primitive
            msp.add_circle((cx, H - cy), radius, dxfattribs={"layer": "EQUIPMENT"})
        elif kind == "ellipse":
            _, cx, cy, rx, ry, fill, stroke, width = primitive
            msp.add_ellipse((cx, H - cy), major_axis=(rx, 0), ratio=(ry / rx if rx else 1.0), dxfattribs={"layer": "EQUIPMENT"})
        elif kind == "polygon":
            _, points, fill, stroke, width = primitive
            msp.add_lwpolyline([(px, H - py) for px, py in points], close=True, dxfattribs={"layer": "EQUIPMENT"})
        elif kind == "text":
            _, x, y, text, size, color, anchor, bold = primitive
            entity = msp.add_text(str(text), dxfattribs={"height": max(size, 1.0), "layer": "TEXT"})
            alignment = TextEntityAlignment.MIDDLE_CENTER if anchor == "middle" else (
                TextEntityAlignment.MIDDLE_RIGHT if anchor == "end" else TextEntityAlignment.MIDDLE_LEFT)
            entity.set_placement((x, H - y), align=alignment)
    doc.saveas(str(path))


def _dexpi_subset(model: dict) -> str:
    nodes = model.get("nodes") or []
    edges = model.get("edges") or []
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- ChemELLM DEXPI-style subset: NOT a certified DEXPI 2.0 document. -->",
        '<DEXPI xmlns="http://www.dexpi.org/schemas/dexpi/2.0" Version="subset-0.1" Diagram="%s">'
        % html.escape(str(model.get("drawing_number") or "PID-001")),
        "  <Equipment>",
    ]
    for node in nodes:
        if node.get("kind") in EQUIPMENT_KINDS | VALVE_KINDS:
            lines.append('    <Equipment id="%s" type="%s" label="%s" spec="%s" material="%s"/>' % (
                html.escape(str(node.get("id"))), html.escape(str(node.get("kind"))),
                html.escape(str(node.get("label") or "")), html.escape(str(node.get("spec") or "")),
                html.escape(str(node.get("material") or ""))))
    lines.append("  </Equipment>")
    lines.append("  <Instrumentation>")
    for node in nodes:
        if node.get("kind") in INSTRUMENT_KINDS:
            lines.append('    <Instrument id="%s" label="%s" type="%s"/>' % (
                html.escape(str(node.get("id"))), html.escape(str(node.get("label") or "")),
                html.escape(str(node.get("type") or ""))))
    lines.append("  </Instrumentation>")
    lines.append("  <PipingNetworkSystem>")
    for edge in edges:
        if (edge.get("kind") or "process") != "process":
            continue
        lines.append('    <Connection from="%s" to="%s" lineNumber="%s" medium="%s"/>' % (
            html.escape(str(edge.get("source"))), html.escape(str(edge.get("target"))),
            html.escape(str(edge.get("line_no") or "")), html.escape(str(edge.get("medium") or ""))))
    lines.append("  </PipingNetworkSystem>")
    lines.append("  <SignalConnections>")
    for edge in edges:
        if (edge.get("kind") or "process") in {"signal", "interlock"}:
            lines.append('    <Signal kind="%s" from="%s" to="%s" label="%s"/>' % (
                html.escape(str(edge.get("kind"))), html.escape(str(edge.get("source"))),
                html.escape(str(edge.get("target"))), html.escape(str(edge.get("label") or ""))))
    lines.append("  </SignalConnections>")
    lines.append("</DEXPI>")
    return "\n".join(lines) + "\n"


def build_pid_files(model: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    W, H, prims = _sheet_primitives(model)
    svg_path = out_dir / "pid.svg"
    pdf_path = out_dir / "pid.pdf"
    dxf_path = out_dir / "pid.dxf"
    json_path = out_dir / "pid.json"
    dexpi_path = out_dir / "pid.dexpi.xml"
    svg_path.write_text(_svg(W, H, prims), encoding="utf-8")
    _pdf(W, H, prims, pdf_path)
    _dxf(W, H, prims, dxf_path)
    json_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    dexpi_path.write_text(_dexpi_subset(model), encoding="utf-8")
    validation = validate_pid(model)
    return {"svg": svg_path, "pdf": pdf_path, "dxf": dxf_path, "json": json_path,
            "dexpi": dexpi_path, "validation": validation}
