"""Standardized P&ID sheet renderer (HG/T 20519 content + ISA-5.1 symbols).

This is a *drawing-quality* upgrade over the earlier schematic renderer:

- equipment symbols: vessel/reactor with 2:1 heads, column with trays/packing,
  shell-and-tube exchanger, pump with motor, tank with dome, compressor;
- ISA-5.1 style instrument bubbles (field / panel), control valves, inline
  manual/check/relief/rupture-disc/blind symbols;
- process lines carry line number, size, piping class, insulation/tracing;
- title block with project/drawing/revision/design-check-approve fields;
- line table, equipment list, instrument list with range, legend and notes.

The original standard text is not available in the workspace, so the sheet is
marked "参照 HG/T 20519 / ISA-5.1 常用表示，条款级合规待核对" — it is a
normalized engineering drawing, not a certified construction drawing.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from pid_sheet import (
    _arrow, _dexpi_subset, _dxf, _pdf, _route, _svg, _table, _text_width,
    validate_pid, EQUIPMENT_KINDS, INSTRUMENT_KINDS, VALVE_KINDS, PALETTE,
    SHEET_W, SHEET_H, FRAME, DRAW_AREA, LINE_TABLE, TITLE_BLOCK,
    REVISION_BLOCK, EQUIP_TABLE, INST_TABLE, LEGEND_AREA, NOTES_AREA,
)

INK = PALETTE["ink"]
MUTED = PALETTE["muted"]
EQ = PALETTE["equipment"]
EQ_STROKE = PALETTE["equipment_stroke"]
SIGNAL = PALETTE["signal"]
INTERLOCK = PALETTE["interlock"]
PROCESS = PALETTE["process"]
PAPER = PALETTE["paper"]
GRID = PALETTE["grid"]

EQUIPMENT_KINDS = set(EQUIPMENT_KINDS) | {"compressor"}
INLINE_VALVES = {"gate", "globe", "ball", "butterfly", "check", "relief", "rupture_disc", "blind", "strainer"}


def _poly(points, fill, stroke, width=0.45):
    return [("polygon", points, fill, stroke, width)]


def _arc(cx, cy, rx, ry, a0, a1, steps=14):
    return [(cx + rx * math.cos(a0 + (a1 - a0) * i / steps),
             cy + ry * math.sin(a0 + (a1 - a0) * i / steps)) for i in range(steps + 1)]


def _nozzle(x, y, dx, dy, length=5.0, flange=3.2, width=0.4):
    out = [("line", x, y, x + dx * length, y + dy * length, EQ_STROKE, width, None)]
    # flange tick perpendicular to nozzle
    px, py = -dy, dx
    out.append(("line", x + dx * length - px * flange, y + dy * length - py * flange,
                x + dx * length + px * flange, y + dy * length + py * flange, EQ_STROKE, width, None))
    return out


def _tag(x, y, tag, label, size=2.8):
    return [("text", x, y, tag, size, INK, "middle", True),
            ("text", x, y + 3.4, label, 2.3, MUTED, "middle", False)]


def _equipment(node, x, y):
    kind = node.get("kind")
    tag = str(node.get("id") or "")
    label = str(node.get("label") or "")
    out: list[tuple] = []
    if kind == "column":
        w, h, hd = 18.0, 44.0, 4.5
        hw = w / 2.0
        top = y - h / 2.0
        bottom = y + h / 2.0
        out.append(("rect", x - hw, top, w, h, EQ, EQ_STROKE, 0.55, 0.0))
        out.extend(_poly(_arc(x, top, hw, hd, math.pi, 2 * math.pi), EQ, EQ_STROKE, 0.55))
        out.extend(_poly(_arc(x, bottom, hw, hd, 0.0, math.pi), EQ, EQ_STROKE, 0.55))
        internals = str(node.get("internals") or "").lower()
        if "packing" in internals or "填料" in internals:
            for i in range(6):
                yy = top + 8 + i * 5.0
                out.append(("line", x - hw + 1.5, yy, x + hw - 1.5, yy - 2.5, EQ_STROKE, 0.2, None))
                out.append(("line", x - hw + 1.5, yy - 2.5, x + hw - 1.5, yy, EQ_STROKE, 0.2, None))
        else:
            for i in range(1, 10):
                yy = top + hd + (h - hd) * i / 10.0
                out.append(("line", x - hw + 1.5, yy, x + hw - 1.5, yy, EQ_STROKE, 0.22, None))
        out.append(("rect", x - hw + 2.5, bottom + hd, w - 5, 5.0, "#d9e6f2", EQ_STROKE, 0.35, 0.0))
        out.extend(_nozzle(x, top - hd, 0, -1, 5.0))
        out.extend(_nozzle(x + hw, y + 4, 1, 0, 5.0))
        out.extend(_nozzle(x, bottom + hd, 0, 1, 5.0))
        out.extend(_nozzle(x - hw, y - 10, -1, 0, 5.0))
        out.extend(_tag(x, top - hd - 5.5, tag, label))
    elif kind in {"reactor", "vessel"}:
        w, h, hd = 26.0, 34.0, 6.5
        hw = w / 2.0
        top = y - h / 2.0
        bottom = y + h / 2.0
        out.append(("rect", x - hw, top, w, h, EQ, EQ_STROKE, 0.55, 0.0))
        out.extend(_poly(_arc(x, top, hw, hd, math.pi, 2 * math.pi), EQ, EQ_STROKE, 0.55))
        out.extend(_poly(_arc(x, bottom, hw, hd, 0.0, math.pi), EQ, EQ_STROKE, 0.55))
        if kind == "reactor" or node.get("agitator"):
            out.append(("rect", x - 5, top - hd - 10, 10, 6, EQ, EQ_STROKE, 0.4, 0.0))
            out.append(("line", x, top - hd, x, top + 12, EQ_STROKE, 0.35, None))
            out.append(("line", x - 4, top + 12, x + 4, top + 12, EQ_STROKE, 0.5, None))
            out.append(("line", x - 2, top + 12, x - 2, top + 15, EQ_STROKE, 0.35, None))
            out.append(("line", x + 2, top + 12, x + 2, top + 15, EQ_STROKE, 0.35, None))
        out.append(("rect", x - 8, bottom + hd, 16, 5, "#d9e6f2", EQ_STROKE, 0.35, 0.0))
        out.extend(_nozzle(x - 3, top - hd, 0, -1, 5.0))
        out.extend(_nozzle(x + hw, y + 2, 1, 0, 5.0))
        out.extend(_nozzle(x + 5, bottom + hd, 0, 1, 5.0))
        out.extend(_tag(x, top - hd - 5.5, tag, label))
    elif kind == "tank":
        w, h = 24.0, 20.0
        hw = w / 2.0
        top = y - h / 2.0
        out.append(("rect", x - hw, top, w, h, EQ, EQ_STROKE, 0.55, 0.0))
        out.extend(_poly(_arc(x, top, hw, 4.0, math.pi, 2 * math.pi), EQ, EQ_STROKE, 0.55))
        out.append(("ellipse", x, y + h / 2.0, hw, 2.5, EQ, EQ_STROKE, 0.45))
        out.extend(_nozzle(x, top - 4.0, 0, -1, 4.0))
        out.extend(_nozzle(x + hw, y + 3, 1, 0, 5.0))
        out.extend(_nozzle(x - 3, y + h / 2.0, 0, 1, 4.0))
        out.extend(_tag(x, top - 8.0, tag, label))
    elif kind == "exchanger":
        w, h = 34.0, 15.0
        hw = w / 2.0
        out.append(("rect", x - hw, y - h / 2.0, w, h, EQ, EQ_STROKE, 0.55, 0.0))
        out.extend(_poly(_arc(x - hw, y, 3.5, h / 2.0, math.pi / 2, 3 * math.pi / 2), EQ, EQ_STROKE, 0.5))
        out.extend(_poly(_arc(x + hw, y, 3.5, h / 2.0, -math.pi / 2, math.pi / 2), EQ, EQ_STROKE, 0.5))
        for yy in (y - 3.5, y, y + 3.5):
            out.append(("line", x - hw + 3.5, yy, x + hw - 3.5, yy, EQ_STROKE, 0.22, None))
        out.extend(_nozzle(x - 8, y - h / 2.0, 0, -1, 5.0))
        out.extend(_nozzle(x + 8, y - h / 2.0, 0, -1, 5.0))
        out.extend(_nozzle(x - 8, y + h / 2.0, 0, 1, 5.0))
        out.extend(_nozzle(x + 8, y + h / 2.0, 0, 1, 5.0))
        out.extend(_tag(x, y - h / 2.0 - 7.0, tag, label))
    elif kind == "pump":
        r = 7.0
        out.append(("circle", x, y, r, EQ, EQ_STROKE, 0.55))
        out.append(("polygon", [(x - 4.0, y - 4.5), (x + 4.0, y), (x - 4.0, y + 4.5)], EQ, EQ_STROKE, 0.4))
        out.append(("rect", x + r + 1, y - 4.5, 8, 9, EQ, EQ_STROKE, 0.4, 0.0))
        out.append(("line", x - r - 5, y, x - r, y, EQ_STROKE, 0.45, None))
        out.append(("line", x, y - r, x, y - r - 5, EQ_STROKE, 0.45, None))
        out.append(("line", x - r - 2, y + r + 2, x + r + 10, y + r + 2, EQ_STROKE, 0.4, None))
        out.extend(_tag(x, y - r - 8.0, tag, label))
    elif kind == "compressor":
        w, h = 26.0, 18.0
        out.append(("rect", x - w / 2, y - h / 2, w, h, EQ, EQ_STROKE, 0.55, 2.0))
        out.append(("circle", x, y, 5.0, EQ, EQ_STROKE, 0.4))
        out.append(("line", x - w / 2 - 5, y, x - w / 2, y, EQ_STROKE, 0.45, None))
        out.append(("line", x + w / 2, y, x + w / 2 + 5, y, EQ_STROKE, 0.45, None))
        out.extend(_tag(x, y - h / 2 - 6.0, tag, label))
    else:
        out.append(("rect", x - 10, y - 10, 20, 20, EQ, EQ_STROKE, 0.5, 0.0))
        out.extend(_tag(x, y - 14, tag, label))
    return out


def _instrument(node, x, y):
    tag = str(node.get("id") or "")
    label = str(node.get("label") or "")
    typ = str(node.get("type") or "")
    r = 7.0
    out = [("circle", x, y, r, PAPER, EQ_STROKE, 0.5)]
    if any(word in typ.upper() for word in ("DCS", "盘", "PANEL", "控制室")):
        out.append(("line", x - r, y, x + r, y, EQ_STROKE, 0.3, None))
    match = re.match(r"^([A-Z]+)-?(\d+[A-Z]?)$", tag)
    if match:
        out.append(("text", x, y - 0.8, match.group(1), 2.8, INK, "middle", True))
        out.append(("text", x, y + 3.4, match.group(2), 2.4, INK, "middle", False))
    else:
        out.append(("text", x, y + 0.8, tag, 2.6, INK, "middle", True))
    if label:
        out.append(("text", x, y + r + 4.0, label, 2.2, MUTED, "middle", False))
    rng = node.get("range")
    if rng:
        out.append(("text", x, y + r + 6.8, str(rng), 1.9, MUTED, "middle", False))
    interlock = node.get("interlock")
    if interlock:
        out.append(("text", x + r + 1.5, y - 2.0, str(interlock), 2.0, INTERLOCK, "start", False))
    return out


def _control_valve(node, x, y):
    tag = str(node.get("id") or "")
    label = str(node.get("label") or "")
    w, h = 8.0, 4.5
    out = [("polygon", [(x - w, y - h), (x, y), (x - w, y + h)], EQ, EQ_STROKE, 0.45),
           ("polygon", [(x + w, y - h), (x, y), (x + w, y + h)], EQ, EQ_STROKE, 0.45),
           ("line", x, y - h, x, y - 8.5, EQ_STROKE, 0.4, None),
           ("ellipse", x, y - 10.0, 4.2, 1.8, PAPER, EQ_STROKE, 0.4)]
    out.append(("text", x, y - 14.0, tag, 2.8, INK, "middle", True))
    if label:
        out.append(("text", x, y + 6.0, label, 2.2, MUTED, "middle", False))
    return out


def _symbol_box(node) -> tuple[float, float]:
    kind = node.get("kind")
    if kind == "column":
        return 9.0, 26.5
    if kind in {"reactor", "vessel"}:
        return 13.0, 23.5
    if kind == "tank":
        return 12.0, 14.0
    if kind == "exchanger":
        return 20.5, 8.5
    if kind == "pump":
        return 13.0, 9.0
    if kind == "compressor":
        return 13.5, 9.0
    return 10.0, 10.0


def _anchor(node, pos, target):
    x, y = pos
    hw, hh = _symbol_box(node)
    if node.get("kind") not in EQUIPMENT_KINDS:
        return pos
    dx, dy = target[0] - x, target[1] - y
    if abs(dx) >= abs(dy):
        return (x + (hw if dx >= 0 else -hw), y)
    return (x, y + (hh if dy >= 0 else -hh))


def _route_detour(p1, p2, obstacles):
    """Orthogonal route with detours around equipment boxes."""
    x1, y1 = p1
    x2, y2 = p2
    candidates = [
        [p1, (x2, y1), p2],
        [p1, (x1, y2), p2],
        [p1, ((x1 + x2) / 2, y1), ((x1 + x2) / 2, y2), p2],
        [p1, (x1, (y1 + y2) / 2), (x2, (y1 + y2) / 2), p2],
    ]
    for pad in (18.0, 32.0, 48.0):
        candidates.extend([
            [p1, (x1, min(y1, y2) - pad), (x2, min(y1, y2) - pad), p2],
            [p1, (x1, max(y1, y2) + pad), (x2, max(y1, y2) + pad), p2],
            [p1, (min(x1, x2) - pad, y1), (min(x1, x2) - pad, y2), p2],
            [p1, (max(x1, x2) + pad, y1), (max(x1, x2) + pad, y2), p2],
        ])
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


def _segment_hits_box(p1, p2, box) -> bool:
    x0, y0, x1, y1 = box
    ax, ay = p1
    bx, by = p2
    if abs(ax - bx) < 1e-6:
        return x0 <= ax <= x1 and not (max(ay, by) < y0 or min(ay, by) > y1)
    if abs(ay - by) < 1e-6:
        return y0 <= ay <= y1 and not (max(ax, bx) < x0 or min(ax, bx) > x1)
    return True


def _rot(points, x, y, angle):
    ca, sa = math.cos(angle), math.sin(angle)
    return [(x + px * ca - py * sa, y + px * sa + py * ca) for px, py in points]


def _inline_valve(kind, tag, x, y, angle):
    out: list[tuple] = []
    # bowtie body along local x-axis
    body = _rot([(-5, -3), (0, 0), (-5, 3)], x, y, angle) + _rot([(5, -3), (0, 0), (5, 3)], x, y, angle)
    out.append(("polygon", _rot([(-5, -3), (0, 0), (-5, 3)], x, y, angle), EQ, EQ_STROKE, 0.4))
    out.append(("polygon", _rot([(5, -3), (0, 0), (5, 3)], x, y, angle), EQ, EQ_STROKE, 0.4))
    if kind == "check":
        tip = _rot([(2.5, 0)], x, y, angle)[0]
        left = _rot([(-2.5, -2.5)], x, y, angle)[0]
        right = _rot([(-2.5, 2.5)], x, y, angle)[0]
        out.append(("polygon", [tip, left, right], EQ_STROKE, EQ_STROKE, 0.2))
    elif kind == "relief":
        # spring-loaded relief: body + outlet stub
        out.append(("line", *_rot([(0, -3)], x, y, angle)[0], *_rot([(0, -8)], x, y, angle)[0], EQ_STROKE, 0.4, None))
        out.append(("line", *_rot([(-2, -8)], x, y, angle)[0], *_rot([(2, -8)], x, y, angle)[0], EQ_STROKE, 0.45, None))
        out.append(("line", *_rot([(5, 3)], x, y, angle)[0], *_rot([(8, 6)], x, y, angle)[0], EQ_STROKE, 0.4, None))
    elif kind == "rupture_disc":
        out.append(("ellipse", x, y, 4.0, 1.2, None, EQ_STROKE, 0.3))
        out.append(("line", *_rot([(-4, 0)], x, y, angle)[0], *_rot([(4, 0)], x, y, angle)[0], EQ_STROKE, 0.3, None))
    elif kind == "blind":
        out.append(("circle", *_rot([(0, 0)], x, y, angle)[0], 4.0, None, EQ_STROKE, 0.35))
        out.append(("circle", *_rot([(7, 0)], x, y, angle)[0], 4.0, None, EQ_STROKE, 0.35))
    elif kind == "strainer":
        out.append(("line", *_rot([(-3, -3)], x, y, angle)[0], *_rot([(3, 3)], x, y, angle)[0], EQ_STROKE, 0.3, None))
        out.append(("line", *_rot([(-3, 3)], x, y, angle)[0], *_rot([(3, -3)], x, y, angle)[0], EQ_STROKE, 0.3, None))
    if tag:
        tx, ty = _rot([(0, -6.5)], x, y, angle)[0]
        out.append(("text", tx, ty, tag, 2.0, INK, "middle", False))
    return out


def _line_label(edge):
    parts = [str(edge.get("line_no") or ""), str(edge.get("spec") or ""),
             str(edge.get("piping_class") or ""), str(edge.get("insulation") or "")]
    text = "  ".join(part for part in parts if part).strip()
    if edge.get("tracing"):
        text = (text + " 伴热").strip()
    return text[:46]


def _title_block(model, prims):
    tx0, ty0, tx1, ty1 = TITLE_BLOCK
    prims.append(("rect", tx0, ty0, tx1 - tx0, ty1 - ty0, None, PALETTE["frame"], 0.7, 0.0))
    prims.append(("line", tx0, ty0 + 14, tx1, ty0 + 14, PALETTE["frame"], 0.35, None))
    prims.append(("line", tx0 + 88, ty0 + 14, tx0 + 88, ty1, PALETTE["frame"], 0.35, None))
    for offset in (24, 34, 44):
        prims.append(("line", tx0, ty0 + offset, tx1, ty0 + offset, PALETTE["frame"], 0.3, None))
    prims.append(("text", tx0 + 3, ty0 + 4, str(model.get("project") or "化工工程"), 2.1, MUTED, "start", False))
    prims.append(("text", tx0 + 3, ty0 + 11, str(model.get("title") or "工艺管道及仪表流程图（P&ID）"), 3.8, INK, "start", True))
    prims.append(("text", tx0 + 3, ty0 + 20, f"图号 {model.get('drawing_number') or '—'}", 2.2, INK, "start", False))
    prims.append(("text", tx0 + 91, ty0 + 20, f"版次 {model.get('revision') or 'A'}", 2.2, INK, "start", False))
    prims.append(("text", tx0 + 3, ty0 + 30, f"设计 {model.get('designer') or '待签'}", 2.1, INK, "start", False))
    prims.append(("text", tx0 + 91, ty0 + 30, f"校核 {model.get('checker') or '待签'}", 2.1, INK, "start", False))
    prims.append(("text", tx0 + 3, ty0 + 40, f"审核 {model.get('approver') or '待签'}", 2.1, INK, "start", False))
    prims.append(("text", tx0 + 91, ty0 + 40, f"批准 {model.get('approver') or '待签'}", 2.1, INK, "start", False))
    prims.append(("text", tx0 + 3, ty0 + 49, f"比例 不按比例（示意）    单位 mm", 2.0, INK, "start", False))
    prims.append(("text", tx0 + 91, ty0 + 49, f"日期 {model.get('date') or '—'}  第1张/共1张", 1.9, INK, "start", False))
    prims.append(("text", tx0 + 3, ty1 - 2.2, "参照 HG/T 20519 内容深度 / ISA-5.1 符号；原文未取得，条款级合规待核对", 1.6, MUTED, "start", False))


def _revision_block(model, prims):
    rx0, ry0, rx1, ry1 = REVISION_BLOCK
    prims.append(("rect", rx0, ry0, rx1 - rx0, ry1 - ry0, None, PALETTE["frame"], 0.5, 0.0))
    prims.append(("line", rx0, ry0 + 8, rx1, ry0 + 8, PALETTE["frame"], 0.3, None))
    prims.append(("text", rx0 + 2, ry0 + 5.5, "版次", 1.9, MUTED, "start", True))
    prims.append(("text", rx0 + 22, ry0 + 5.5, "日期", 1.9, MUTED, "start", True))
    prims.append(("text", rx0 + 58, ry0 + 5.5, "修改说明", 1.9, MUTED, "start", True))
    prims.append(("text", rx0 + 2, ry0 + 14.5, str(model.get("revision") or "A"), 2.1, INK, "start", False))
    prims.append(("text", rx0 + 22, ry0 + 14.5, str(model.get("date") or "—"), 1.9, INK, "start", False))
    prims.append(("text", rx0 + 58, ry0 + 14.5, str(model.get("revision_note") or "初版"), 1.9, INK, "start", False))


def _legend(prims):
    lx, ly, lrx, lry = LEGEND_AREA
    prims.append(("text", lx, ly - 2.0, "图例", 3.0, INK, "start", True))
    items = [
        ("工艺管线", "process"), ("仪表信号", "signal"), ("联锁", "interlock"),
        ("现场仪表", "instrument"), ("盘装仪表", "panel"), ("控制阀", "valve"),
        ("手动阀", "manual"), ("止回阀", "check"), ("安全阀", "relief"),
        ("爆破片", "rupture"), ("盲板", "blind"),
    ]
    for index, (label, kind) in enumerate(items):
        col, row = index % 2, index // 2
        sx = lx + 8 + col * 103
        sy = ly + 8 + row * 10.5
        if kind == "process":
            prims.append(("line", sx - 7, sy, sx + 7, sy, PROCESS, 0.55, None))
        elif kind == "signal":
            prims.append(("line", sx - 7, sy, sx + 7, sy, SIGNAL, 0.3, "dash"))
        elif kind == "interlock":
            prims.append(("line", sx - 7, sy, sx + 7, sy, INTERLOCK, 0.35, "dashdot"))
        elif kind in {"instrument", "panel"}:
            prims.append(("circle", sx, sy, 3.8, PAPER, EQ_STROKE, 0.4))
            if kind == "panel":
                prims.append(("line", sx - 3.8, sy, sx + 3.8, sy, EQ_STROKE, 0.25, None))
        elif kind in {"valve", "manual", "check", "relief", "rupture", "blind"}:
            prims.extend(_inline_valve({"check": "check", "relief": "relief", "rupture": "rupture_disc",
                                        "blind": "blind"}.get(kind, "gate"), "", sx, sy, 0.0))
            if kind == "valve":
                prims.append(("line", sx, sy - 4, sx, sy - 7, EQ_STROKE, 0.35, None))
                prims.append(("ellipse", sx, sy - 8.2, 2.6, 1.1, PAPER, EQ_STROKE, 0.3))
        prims.append(("text", sx + 12, sy + 1.0, label, 2.1, INK, "start", False))


def _sheet(model):
    W, H = SHEET_W, SHEET_H
    prims: list[tuple] = [
        ("rect", 0, 0, W, H, PAPER, None, 0.0, 0.0),
        ("rect", FRAME[0], FRAME[1], FRAME[2] - FRAME[0], FRAME[3] - FRAME[1], None, PALETTE["frame"], 0.7, 0.0),
        ("rect", FRAME[0] + 4, FRAME[1] + 4, FRAME[2] - FRAME[0] - 8, FRAME[3] - FRAME[1] - 8, None, PALETTE["frame"], 0.35, 0.0),
    ]
    inner = (FRAME[0] + 4, FRAME[1] + 4, FRAME[2] - 4, FRAME[3] - 4)
    cols, rows = 8, 6
    for index in range(cols + 1):
        x = inner[0] + (inner[2] - inner[0]) * index / cols
        prims.append(("line", x, inner[1], x, inner[1] + 3, PALETTE["frame"], 0.35, None))
        prims.append(("line", x, inner[3] - 3, x, inner[3], PALETTE["frame"], 0.35, None))
    for index in range(cols):
        x = inner[0] + (inner[2] - inner[0]) * (index + 0.5) / cols
        prims.append(("text", x, inner[1] + 2.4, str(index + 1), 2.2, MUTED, "middle", False))
        prims.append(("text", x, inner[3] - 0.8, str(index + 1), 2.2, MUTED, "middle", False))
    for index in range(rows + 1):
        y = inner[1] + (inner[3] - inner[1]) * index / rows
        prims.append(("line", inner[0], y, inner[0] + 3, y, PALETTE["frame"], 0.35, None))
        prims.append(("line", inner[2] - 3, y, inner[2], y, PALETTE["frame"], 0.35, None))
    for index in range(rows):
        y = inner[1] + (inner[3] - inner[1]) * (index + 0.5) / rows
        letter = chr(ord("A") + index)
        prims.append(("text", inner[0] + 1.5, y, letter, 2.2, MUTED, "middle", False))
        prims.append(("text", inner[2] - 1.5, y, letter, 2.2, MUTED, "middle", False))

    _title_block(model, prims)
    _revision_block(model, prims)
    _legend(prims)

    area = DRAW_AREA
    prims.append(("rect", area[0], area[1], area[2] - area[0], area[3] - area[1], None, GRID, 0.25, 0.0))
    nodes = [node for node in (model.get("nodes") or []) if node.get("id")]
    xs = [float(node.get("x", 0.0)) for node in nodes] or [0.0]
    ys = [float(node.get("y", 0.0)) for node in nodes] or [0.0]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    pad = 26.0
    scale = min((area[2] - area[0] - 2 * pad) / span_x, (area[3] - area[1] - 2 * pad) / span_y)
    ox = area[0] + pad + ((area[2] - area[0] - 2 * pad) - span_x * scale) / 2.0
    oy = area[1] + pad + ((area[3] - area[1] - 2 * pad) - span_y * scale) / 2.0
    positions = {str(node["id"]): (ox + (float(node.get("x", 0.0)) - minx) * scale,
                                   oy + (float(node.get("y", 0.0)) - miny) * scale) for node in nodes}

    boxes = []
    box_map = {}
    for node in nodes:
        kind = node.get("kind")
        if kind in EQUIPMENT_KINDS:
            x, y = positions[str(node["id"])]
            hw, hh = _symbol_box(node)
            box = (x - hw - 2.0, y - hh - 2.0, x + hw + 2.0, y + hh + 2.0)
            boxes.append(box)
            box_map[str(node["id"])] = box
    prims.append(("text", area[0] + 2, area[1] - 4.0,
                  "工艺管道及仪表流程图（P&ID）　参照 HG/T 20519 内容深度 / ISA-5.1 符号　不按比例", 2.8, INK, "start", True))

    for node in nodes:
        x, y = positions[str(node["id"])]
        kind = node.get("kind")
        if kind in EQUIPMENT_KINDS:
            prims.extend(_equipment(node, x, y))
        elif kind in INSTRUMENT_KINDS:
            prims.extend(_instrument(node, x, y))
        elif kind in VALVE_KINDS:
            prims.extend(_control_valve(node, x, y))

    edge_rows = []
    for edge in model.get("edges") or []:
        source = positions.get(str(edge.get("source")))
        target = positions.get(str(edge.get("target")))
        if not source or not target:
            continue
        kind = edge.get("kind") or "process"
        if kind == "process":
            src_node = next((n for n in nodes if str(n.get("id")) == str(edge.get("source"))), None)
            tgt_node = next((n for n in nodes if str(n.get("id")) == str(edge.get("target"))), None)
            src_anchor = _anchor(src_node, source, target) if src_node else source
            tgt_anchor = _anchor(tgt_node, target, source) if tgt_node else target
            obstacles = [box for node_id, box in box_map.items()
                         if node_id not in {str(edge.get("source")), str(edge.get("target"))}]
            points = _route_detour(src_anchor, tgt_anchor, obstacles)
            for a, b in zip(points, points[1:]):
                prims.append(("line", a[0], a[1], b[0], b[1], PROCESS, 0.55, None))
            prims.extend(_arrow(points[-2][0], points[-2][1], points[-1][0], points[-1][1], PROCESS))
            label = _line_label(edge)
            if label:
                best = max(zip(points, points[1:]), key=lambda pair: abs(pair[0][0] - pair[1][0]) + abs(pair[0][1] - pair[1][1]))
                mx, my = (best[0][0] + best[1][0]) / 2.0, (best[0][1] + best[1][1]) / 2.0 - 2.0
                width = _text_width(label, 2.0) + 2.0
                prims.append(("rect", mx - width / 2, my - 2.5, width, 3.2, PAPER, None, 0.0, 0.0))
                prims.append(("text", mx, my, label, 2.0, MUTED, "middle", False))
            for index, valve in enumerate(edge.get("valves") or []):
                vkind = str(valve.get("type") or "gate").lower()
                if vkind not in INLINE_VALVES:
                    continue
                fraction = float(valve.get("position", 0.5))
                # walk route by length
                lengths = [abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(points, points[1:])]
                total = sum(lengths) or 1.0
                want = total * min(max(fraction, 0.05), 0.95)
                acc = 0.0
                for (a, b), seg in zip(zip(points, points[1:]), lengths):
                    if acc + seg >= want:
                        t = (want - acc) / seg if seg else 0.0
                        vx, vy = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
                        angle = math.atan2(b[1] - a[1], b[0] - a[0])
                        prims.extend(_inline_valve(vkind, str(valve.get("tag") or ""), vx, vy, angle))
                        break
                    acc += seg
            edge_rows.append({
                "line_no": str(edge.get("line_no") or "—"), "source": str(edge.get("source")),
                "target": str(edge.get("target")), "medium": str(edge.get("medium") or edge.get("label") or "—"),
                "spec": str(edge.get("spec") or "—"), "piping_class": str(edge.get("piping_class") or "—"),
                "insulation": str(edge.get("insulation") or "—"),
            })
        else:
            color = SIGNAL if kind == "signal" else INTERLOCK
            dash = "dash" if kind == "signal" else "dashdot"
            src_node = next((n for n in nodes if str(n.get("id")) == str(edge.get("source"))), None)
            tgt_node = next((n for n in nodes if str(n.get("id")) == str(edge.get("target"))), None)
            src_anchor = _anchor(src_node, source, target) if src_node else source
            tgt_anchor = _anchor(tgt_node, target, source) if tgt_node else target
            obstacles = [box for node_id, box in box_map.items()
                         if node_id not in {str(edge.get("source")), str(edge.get("target"))}]
            points = _route_detour(src_anchor, tgt_anchor, obstacles)
            for a, b in zip(points, points[1:]):
                prims.append(("line", a[0], a[1], b[0], b[1], color, 0.3, dash))
            label = str(edge.get("label") or "")
            if label:
                mx, my = (source[0] + target[0]) / 2.0, (source[1] + target[1]) / 2.0
                prims.append(("text", mx, my - 1.5, label, 2.0, color, "middle", False))

    equip_rows = [[str(node.get("id")), str(node.get("label") or ""), str(node.get("spec") or "—"),
                   str(node.get("material") or "—"), "1"] for node in nodes if node.get("kind") in EQUIPMENT_KINDS]
    _table(prims, EQUIP_TABLE, "设备一览表", ["位号", "名称", "规格/参数", "材料", "数量"], equip_rows, [22, 30, 34, 16, 8])
    inst_rows = []
    for node in nodes:
        if node.get("kind") in INSTRUMENT_KINDS:
            inst_rows.append([str(node.get("id")), str(node.get("label") or ""), str(node.get("type") or "—"),
                              str(node.get("range") or "—"), str(node.get("interlock") or "—")])
        elif node.get("kind") in VALVE_KINDS:
            inst_rows.append([str(node.get("id")), str(node.get("label") or "控制阀"), "控制阀",
                              str(node.get("spec") or "—"), str(node.get("interlock") or "—")])
    for edge in model.get("edges") or []:
        for valve in edge.get("valves") or []:
            inst_rows.append([str(valve.get("tag") or "—"), str(valve.get("type") or "手动阀"),
                              "管道阀", str(valve.get("spec") or "—"), str(valve.get("interlock") or "—")])
    _table(prims, INST_TABLE, "仪表与阀门", ["位号", "说明", "类型", "量程/规格", "联锁"], inst_rows, [22, 30, 20, 22, 18])
    line_rows = [[row["line_no"], row["source"], row["target"], row["spec"], row["piping_class"], row["medium"], row["insulation"]]
                 for row in edge_rows][:8]
    _table(prims, LINE_TABLE, "管线表", ["管线号", "起点", "终点", "规格", "等级", "介质", "保温"], line_rows, [26, 18, 18, 14, 14, 20, 12])
    if len(edge_rows) > len(line_rows):
        prims.append(("text", LINE_TABLE[0], LINE_TABLE[3] + 3.5,
                      f"（共 {len(edge_rows)} 条工艺管线，本表显示前 {len(line_rows)} 条）", 2.0, MUTED, "start", False))

    nx, ny, nrx, nry = NOTES_AREA
    prims.append(("text", nx, ny - 2.0, "说明", 3.0, INK, "start", True))
    notes = [
        "1. 本图参照 HG/T 20519 内容深度与 ISA-5.1 符号体系绘制；标准原文未取得，条款级合规待核对。",
        "2. P&ID 不按比例；管线号/管道等级/位号来自模型，缺失处以“—”表示，需设计与校核确认。",
        "3. 工艺管线为示意路由，交叉线未做断线；手动阀/仪表位置需按实际布置调整。",
        "4. 本图不能直接作为施工图，须经设计、校核、审核签署；未注尺寸单位 mm。",
    ]
    for index, note in enumerate(notes):
        prims.append(("text", nx, ny + 6 + index * 7.5, note, 1.9, MUTED, "start", False))
    return W, H, prims


def validate_standard(model: dict) -> dict:
    """Technical-content checks on top of the structural validator."""
    validation = validate_pid(model)
    warnings = list(validation.get("warnings") or [])
    for key, label in (("project", "项目名称"), ("date", "日期"), ("designer", "设计"),
                       ("checker", "校核"), ("approver", "审核"), ("revision_note", "修改说明")):
        if not str(model.get(key) or "").strip():
            warnings.append(f"标题栏缺少 {label}")
    for edge in model.get("edges") or []:
        if (edge.get("kind") or "process") != "process":
            continue
        line = str(edge.get("line_no") or "—")
        if not edge.get("spec"):
            warnings.append(f"{line}：缺少管道规格/管径（spec）")
        if not edge.get("piping_class"):
            warnings.append(f"{line}：缺少管道等级（piping_class）")
        if not edge.get("medium"):
            warnings.append(f"{line}：缺少介质（medium）")
        for valve in edge.get("valves") or []:
            kind = str(valve.get("type") or "").lower()
            if kind not in INLINE_VALVES:
                warnings.append(f"{line}：不支持的管道阀类型 {kind}")
            if not valve.get("tag"):
                warnings.append(f"{line}：管道阀缺少位号")
    for node in model.get("nodes") or []:
        if node.get("kind") == "instrument" and not node.get("range"):
            warnings.append(f"{node.get('id')}：仪表缺少量程/规格（range）")
        if node.get("kind") in EQUIPMENT_KINDS and not node.get("material"):
            warnings.append(f"{node.get('id')}：设备缺少材料（material）")
    return {**validation, "warnings": warnings,
            "standard": "HG/T 20519 内容深度 + ISA-5.1 符号（参照，待核对）"}


def build_pid_files(model: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    W, H, prims = _sheet(model)
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
    return {"svg": svg_path, "pdf": pdf_path, "dxf": dxf_path, "json": json_path,
            "dexpi": dexpi_path, "validation": validate_standard(model)}
