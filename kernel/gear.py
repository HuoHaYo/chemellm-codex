"""Spur gear part drawing + CAD model (extension of `cad_part`).

Scope and honesty:
- external spur gear, standard full-depth basic rack profile (20° default),
  no profile shift, no helix, no backlash/allowance modification;
- the tooth profile is a true involute from the base circle, with a simplified
  root transition (radial/root-arc approximation) — good enough for a part
  drawing preview, not for manufacturing without a gear specialist's check;
- basic rack profile GB/T 1356 and accuracy GB/T 10095 are referenced but not
  copied from the original standard text, so they are marked "待核对".

Outputs: A3 part drawing (SVG/PDF), DXF, JSON data, plus FCStd/STEP via FreeCAD.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from pid_sheet import _pdf, _svg

DENSITY = 7850e-9  # kg/mm3
SCALES = [1.0, 2.0, 2.5, 5.0, 10.0]


def _num(value, default=None):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def involute(alpha: float) -> float:
    return math.tan(alpha) - alpha


def _half_angle(z: float, alpha: float, alpha_r: float) -> float:
    return math.pi / (2.0 * z) + involute(alpha) - involute(alpha_r)


def profile_points(m: float, z: int, alpha_deg: float, samples: int = 18) -> tuple[list[tuple[float, float]], dict]:
    alpha = math.radians(alpha_deg)
    d = m * z
    rb = d / 2.0 * math.cos(alpha)
    ra = m * (z + 2.0) / 2.0
    rf = m * (z - 2.5) / 2.0
    if rf <= 0:
        raise ValueError("齿数过少：齿根圆直径必须为正")
    inv_alpha = involute(alpha)
    r_start = max(rb, rf)
    alpha_start = math.acos(min(1.0, rb / r_start)) if r_start > 0 else 0.0
    half_start = _half_angle(z, alpha, alpha_start)
    alpha_tip = math.acos(min(1.0, rb / ra))
    half_tip = _half_angle(z, alpha, alpha_tip)
    points: list[tuple[float, float]] = []
    for index in range(z):
        theta = 2.0 * math.pi * index / z
        # right flank: root/base -> tip
        for sample in range(samples + 1):
            radius = r_start + (ra - r_start) * sample / samples
            alpha_r = math.acos(min(1.0, rb / radius))
            half = _half_angle(z, alpha, alpha_r)
            angle = theta - half
            points.append((radius * math.cos(angle), radius * math.sin(angle)))
        # tip arc
        for sample in range(1, 5):
            angle = theta - half_tip + 2.0 * half_tip * sample / 4.0
            points.append((ra * math.cos(angle), ra * math.sin(angle)))
        # left flank: tip -> root/base
        for sample in range(samples, -1, -1):
            radius = r_start + (ra - r_start) * sample / samples
            alpha_r = math.acos(min(1.0, rb / radius))
            half = _half_angle(z, alpha, alpha_r)
            angle = theta + half
            points.append((radius * math.cos(angle), radius * math.sin(angle)))
        # root transition to next tooth
        theta_next = theta + 2.0 * math.pi / z
        if rf < rb:
            points.append((rf * math.cos(theta + half_start), rf * math.sin(theta + half_start)))
            for sample in range(1, 7):
                angle = (theta + half_start) + ((theta_next - half_start) - (theta + half_start)) * sample / 6.0
                points.append((rf * math.cos(angle), rf * math.sin(angle)))
            points.append((rf * math.cos(theta_next - half_start), rf * math.sin(theta_next - half_start)))
        else:
            half_root = _half_angle(z, alpha, 0.0)
            for sample in range(1, 7):
                angle = (theta + half_root) + ((theta_next - half_root) - (theta + half_root)) * sample / 6.0
                points.append((rf * math.cos(angle), rf * math.sin(angle)))
    return points, {
        "module_mm": m, "teeth": z, "pressure_angle_deg": alpha_deg,
        "pitch_diameter_mm": round(d, 3), "tip_diameter_mm": round(2.0 * ra, 3),
        "root_diameter_mm": round(2.0 * rf, 3), "base_diameter_mm": round(2.0 * rb, 3),
        "circular_pitch_mm": round(math.pi * m, 3),
    }


def _area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _params(args: dict) -> dict:
    module = _num(args.get("module_mm"), 2.0)
    teeth = int(_num(args.get("teeth"), 20))
    alpha = _num(args.get("pressure_angle_deg"), 20.0)
    width = _num(args.get("face_width_mm"), 20.0)
    if not (0.3 <= module <= 20.0):
        raise ValueError("module_mm 应在 0.3~20 mm")
    if not (8 <= teeth <= 200):
        raise ValueError("teeth 应在 8~200")
    if abs(alpha - 14.5) > 1e-6 and abs(alpha - 20.0) > 1e-6 and abs(alpha - 25.0) > 1e-6:
        raise ValueError("pressure_angle_deg 仅支持 14.5/20/25（GB/T 1356 基本齿廓，待核对）")
    if not (2.0 <= width <= 300.0):
        raise ValueError("face_width_mm 应在 2~300 mm")
    d = module * teeth
    bore = _num(args.get("bore_mm"), max(6.0, round(d * 0.2)))
    if not (3.0 <= bore <= d * 0.75):
        raise ValueError("bore_mm 应在 3~0.75d 之间")
    hub_diameter = _num(args.get("hub_diameter_mm"), max(bore + 10.0, bore * 1.8))
    if not (bore + 2.0 <= hub_diameter <= d):
        raise ValueError("hub_diameter_mm 应大于孔径且不大于分度圆直径")
    hub_width = _num(args.get("hub_width_mm"), width + 10.0)
    if not (width <= hub_width <= width + 120.0):
        raise ValueError("hub_width_mm 应不小于齿宽且不超过齿宽+120")
    keyway_width = _num(args.get("keyway_width_mm"), 0.0) or 0.0
    keyway_depth = _num(args.get("keyway_depth_mm"), 0.0) or 0.0
    if (keyway_width == 0) != (keyway_depth == 0):
        raise ValueError("keyway_width_mm 与 keyway_depth_mm 必须同时给出或同时为 0")
    if keyway_width and not (1.0 <= keyway_width <= 0.35 * bore):
        raise ValueError("keyway_width_mm 超出 1~0.35·bore")
    if keyway_depth and not (0.5 <= keyway_depth <= 0.25 * bore):
        raise ValueError("keyway_depth_mm 超出 0.5~0.25·bore")
    accuracy = str(args.get("accuracy_grade") or "8")
    if accuracy not in {"6", "7", "8", "9", "10"}:
        raise ValueError("accuracy_grade 应为 6~10（GB/T 10095，待核对）")
    points, calc = profile_points(module, teeth, alpha)
    area = _area(points)
    bore_area = math.pi / 4.0 * bore ** 2
    key_area = keyway_width * keyway_depth if keyway_width else 0.0
    weight = (area - bore_area - key_area) * width * DENSITY
    calc.update({
        "face_width_mm": round(width, 2), "bore_mm": round(bore, 2),
        "hub_diameter_mm": round(hub_diameter, 2), "hub_width_mm": round(hub_width, 2),
        "keyway_width_mm": round(keyway_width, 2), "keyway_depth_mm": round(keyway_depth, 2),
        "accuracy_grade": accuracy, "material": str(args.get("material") or "45钢"),
        "weight_kg": round(weight, 3),
        "formula": "渐开线标准直齿圆柱齿轮：d=mz，da=m(z+2)，df=m(z−2.5)，db=d·cosα；重量按齿廓面积×齿宽×7850 kg/m3（不含轮毂/倒角）",
    })
    return {
        "equipment_type": "gear", "tag": str(args.get("tag") or "GEAR-001"),
        "module_mm": module, "teeth": teeth, "pressure_angle_deg": alpha,
        "face_width_mm": width, "bore_mm": bore,
        "hub_diameter_mm": hub_diameter, "hub_width_mm": hub_width,
        "keyway_width_mm": keyway_width, "keyway_depth_mm": keyway_depth,
        "accuracy_grade": accuracy, "material": str(args.get("material") or "45钢"),
        "points": points, "calculation": calc,
    }


def _circle(prims, cx, cy, radius, color, width, dash=None):
    if dash:
        segments = 72
        for index in range(segments):
            a1 = 2.0 * math.pi * index / segments
            a2 = 2.0 * math.pi * (index + 1) / segments
            prims.append(("line", cx + radius * math.cos(a1), cy + radius * math.sin(a1),
                          cx + radius * math.cos(a2), cy + radius * math.sin(a2), color, width, dash))
    else:
        prims.append(("circle", cx, cy, radius, None, color, width))


def _arrow(prims, x1, y1, x2, y2, color):
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 1.8
    left = (x2 - size * math.cos(angle - 0.45), y2 - size * math.sin(angle - 0.45))
    right = (x2 - size * math.cos(angle + 0.45), y2 - size * math.sin(angle + 0.45))
    prims.append(("polygon", [left, right, (x2, y2)], color, color, 0.2))


def _drawing(params: dict) -> tuple[float, float, list[tuple]]:
    W, H = 420.0, 297.0
    ink, muted, grid, paper, fill = "#1f3b57", "#5a6b7d", "#90a4ae", "#ffffff", "#eef4fb"
    calc = params["calculation"]
    da = calc["tip_diameter_mm"]
    d = calc["pitch_diameter_mm"]
    df = calc["root_diameter_mm"]
    db = calc["base_diameter_mm"]
    bore = params["bore_mm"]
    hub_d = params["hub_diameter_mm"]
    width = params["face_width_mm"]
    hub_w = params["hub_width_mm"]
    kw = params["keyway_width_mm"]
    kd = params["keyway_depth_mm"]

    scale = 1.0 / SCALES[-1]
    for denominator in SCALES:
        if da * (1.0 / denominator) <= 150.0 and width * (1.0 / denominator) <= 60.0:
            scale = 1.0 / denominator
            break

    prims: list[tuple] = [
        ("rect", 0, 0, W, H, paper, None, 0.0, 0.0),
        ("rect", 25, 10, W - 35, H - 20, None, ink, 0.6, 0.0),
    ]
    cx, cy = 125.0, 135.0
    r = da / 2.0 * scale
    prims.append(("text", 40, 55, f"{params['tag']}  直齿圆柱齿轮  (端面)", 4.0, ink, "start", True))
    # involute teeth
    points = [(cx + x * scale, cy - y * scale) for x, y in params["points"]]
    prims.append(("polygon", points, fill, ink, 0.55))
    _circle(prims, cx, cy, d / 2.0 * scale, muted, 0.3, "dash")
    _circle(prims, cx, cy, df / 2.0 * scale, muted, 0.25, "dash")
    _circle(prims, cx, cy, db / 2.0 * scale, grid, 0.2, "dashdot")
    prims.append(("circle", cx, cy, bore / 2.0 * scale, paper, ink, 0.5))
    if kw:
        prims.append(("rect", cx - kw / 2.0 * scale, cy - (bore / 2.0 + kd) * scale,
                      kw * scale, kd * scale, paper, ink, 0.45, 0.0))
    prims.append(("line", cx - r - 12, cy, cx + r + 12, cy, muted, 0.25, "dashdot"))
    prims.append(("line", cx, cy - r - 12, cx, cy + r + 12, muted, 0.25, "dashdot"))
    # dimensions
    dim_y = cy + r + 16
    prims.append(("line", cx - r, dim_y, cx + r, dim_y, muted, 0.25, None))
    _arrow(prims, cx - r, dim_y, cx + r, dim_y, muted)
    _arrow(prims, cx + r, dim_y, cx - r, dim_y, muted)
    prims.append(("text", cx, dim_y + 5, f"φ{da:.1f}", 3.0, muted, "middle", False))
    prims.append(("text", cx, cy + r + 26, f"分度圆 φ{d:.2f}  齿根圆 φ{df:.2f}", 2.6, muted, "middle", False))

    # side view: width x tip diameter, hub
    sx, sy = 295.0, 135.0
    prims.append(("text", sx - 45, 55, "侧视（轮毂/齿宽）", 3.0, ink, "start", True))
    prims.append(("rect", sx - width * scale / 2, sy - r, width * scale, 2 * r, fill, ink, 0.5, 0.0))
    prims.append(("rect", sx - hub_w * scale / 2, sy - hub_d / 2 * scale, hub_w * scale, hub_d * scale, "#d9e6f2", ink, 0.5, 0.0))
    prims.append(("line", sx - bore / 2 * scale, sy - r - 5, sx - bore / 2 * scale, sy + r + 5, muted, 0.3, "dash"))
    prims.append(("line", sx + bore / 2 * scale, sy - r - 5, sx + bore / 2 * scale, sy + r + 5, muted, 0.3, "dash"))
    if kw:
        prims.append(("rect", sx - kw / 2 * scale, sy - (bore / 2 + kd) * scale, kw * scale, kd * scale, paper, ink, 0.4, 0.0))
    prims.append(("line", sx - hub_w * scale / 2 - 12, sy, sx + hub_w * scale / 2 + 12, sy, muted, 0.25, "dashdot"))
    dim_y2 = sy + r + 12
    prims.append(("line", sx - width * scale / 2, dim_y2, sx + width * scale / 2, dim_y2, muted, 0.25, None))
    _arrow(prims, sx - width * scale / 2, dim_y2, sx + width * scale / 2, dim_y2, muted)
    _arrow(prims, sx + width * scale / 2, dim_y2, sx - width * scale / 2, dim_y2, muted)
    prims.append(("text", sx, dim_y2 + 5, f"b={width:.1f}", 2.8, muted, "middle", False))

    # parameter table (two blocks to stay inside A3 sheet)
    table_y = 190.0
    rows = [
        ("模数 m", f"{params['module_mm']:.2f} mm"),
        ("齿数 z", str(params["teeth"])),
        ("压力角 α", f"{params['pressure_angle_deg']:.1f}°"),
        ("分度圆 d", f"φ{d:.2f} mm"),
        ("齿顶圆 da", f"φ{da:.2f} mm"),
        ("齿根圆 df", f"φ{df:.2f} mm"),
        ("基圆 db", f"φ{db:.2f} mm"),
        ("齿宽 b", f"{width:.1f} mm"),
        ("孔径 d0", f"φ{bore:.1f} mm"),
        ("轮毂 φD×b", f"φ{hub_d:.1f}×{hub_w:.1f} mm"),
        ("键槽 b×t", f"{kw:.1f}×{kd:.1f} mm" if kw else "无"),
        ("精度/材料", f"{params['accuracy_grade']}级 / {params['material']}"),
        ("重量（估）", f"{calc['weight_kg']:.2f} kg"),
        ("比例", f"1:{int(round(1.0 / scale))}"),
    ]
    prims.append(("text", 35, table_y - 5, "齿轮参数表", 3.0, ink, "start", True))
    col_w = [42.0, 50.0]
    block_w = sum(col_w)
    for block_index, block_rows in enumerate((rows[:7], rows[7:])):
        bx = 35.0 + block_index * (block_w + 6.0)
        yy = table_y
        prims.append(("rect", bx, yy, block_w, 6.5, "#dce8f4", ink, 0.35, 0.0))
        prims.append(("text", bx + 1, yy + 4.6, "项目", 2.4, ink, "start", True))
        prims.append(("text", bx + col_w[0] + 1, yy + 4.6, "数值", 2.4, ink, "start", True))
        yy += 6.5
        for row_index, (key, value) in enumerate(block_rows):
            prims.append(("rect", bx, yy, block_w, 5.9, None if row_index % 2 else "#f5f8fb", grid, 0.25, 0.0))
            prims.append(("text", bx + 1, yy + 4.2, key, 2.3, muted, "start", False))
            prims.append(("text", bx + col_w[0] + 1, yy + 4.2, value, 2.3, ink, "start", False))
            yy += 5.9
        prims.append(("rect", bx, table_y, block_w, yy - table_y, None, ink, 0.4, 0.0))

    notes = [
        "注：1. 标准直齿圆柱齿轮，基本齿廓 α=20°（GB/T 1356，原文待核对）；无变位、无螺旋角。",
        "2. 齿廓为渐开线（基圆→齿顶），齿根过渡为简化径向/圆弧近似，未计齿根圆角与修形。",
        "3. 精度等级按 GB/T 10095（待核对）；本图不含公差、材料热处理与检验要求。",
        "4. 重量按齿廓面积×齿宽×7850 kg/m3 估算；本图不替代制造图。",
    ]
    for index, note in enumerate(notes):
        prims.append(("text", 35, 250 + index * 5.0, note, 2.0, muted, "start", False))

    # title block
    tx, ty = 248.0, 245.0
    prims.append(("rect", tx, ty, 147, 42, None, ink, 0.6, 0.0))
    prims.append(("line", tx, ty + 16, tx + 147, ty + 16, ink, 0.4, None))
    prims.append(("line", tx, ty + 28, tx + 147, ty + 28, ink, 0.4, None))
    prims.append(("text", tx + 3, ty + 10, f"{params['tag']} 齿轮零件图", 4.0, ink, "start", True))
    prims.append(("text", tx + 3, ty + 22, f"图号 DWG-{params['tag']}    版本 A", 2.4, muted, "start", False))
    prims.append(("text", tx + 3, ty + 31, "设计 ChemELLM    校核 待校核", 2.3, ink, "start", False))
    prims.append(("text", tx + 3, ty + 39, f"比例 1:{int(round(1.0 / scale))}    单位 mm    日期 2026-10-22", 2.1, muted, "start", False))
    return W, H, prims


def _freecad_script(params: dict, fcstd: Path, step: Path) -> str:
    return f'''
import FreeCAD
import Part
import json

P = {json.dumps(params, ensure_ascii=False)}
pts = [FreeCAD.Vector(x, y, 0.0) for x, y in P["points"]]
wire = Part.makePolygon(pts + [pts[0]])
face = Part.Face(wire)
shape = face.extrude(FreeCAD.Vector(0, 0, P["face_width_mm"]))
bore = Part.makeCylinder(P["bore_mm"] / 2.0, P["face_width_mm"] + 2.0,
                         FreeCAD.Vector(0, 0, -1.0), FreeCAD.Vector(0, 0, 1))
shape = shape.cut(bore)
if P["keyway_width_mm"] > 0:
    box = Part.makeBox(P["keyway_width_mm"], P["keyway_depth_mm"], P["face_width_mm"] + 2.0,
                       FreeCAD.Vector(-P["keyway_width_mm"] / 2.0, P["bore_mm"] / 2.0, -1.0))
    shape = shape.cut(box)
shape = shape.removeSplitter()
doc = FreeCAD.newDocument("ChemELLM_Gear")
obj = doc.addObject("Part::Feature", P["tag"])
obj.Shape = shape
doc.recompute()
doc.saveAs({fcstd.as_posix()!r})
Part.export([obj], {step.as_posix()!r})
'''


def _dxf(params: dict, path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    for name, color in (("OUTLINE", 7), ("PITCH", 4), ("BORE", 1), ("CENTER", 8), ("TEXT", 2)):
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    points = [(x, y) for x, y in params["points"]]
    msp.add_lwpolyline(points, close=True, dxfattribs={"layer": "OUTLINE"})
    calc = params["calculation"]
    for radius, layer, linetype in (
        (calc["pitch_diameter_mm"] / 2.0, "PITCH", "DASHED"),
        (calc["root_diameter_mm"] / 2.0, "OUTLINE", "DASHED"),
        (calc["base_diameter_mm"] / 2.0, "CENTER", "CENTER"),
    ):
        msp.add_circle((0, 0), radius, dxfattribs={"layer": layer, "linetype": linetype})
    msp.add_circle((0, 0), params["bore_mm"] / 2.0, dxfattribs={"layer": "BORE"})
    if params["keyway_width_mm"]:
        kw, kd = params["keyway_width_mm"], params["keyway_depth_mm"]
        msp.add_lwpolyline([(-kw / 2, params["bore_mm"] / 2.0), (-kw / 2, params["bore_mm"] / 2.0 + kd),
                            (kw / 2, params["bore_mm"] / 2.0 + kd), (kw / 2, params["bore_mm"] / 2.0)],
                           close=True, dxfattribs={"layer": "BORE"})
    msp.add_line((-calc["tip_diameter_mm"], 0), (calc["tip_diameter_mm"], 0), dxfattribs={"layer": "CENTER"})
    msp.add_line((0, -calc["tip_diameter_mm"]), (0, calc["tip_diameter_mm"]), dxfattribs={"layer": "CENTER"})
    msp.add_text(f"{params['tag']} 直齿圆柱齿轮 m={params['module_mm']} z={params['teeth']}",
                 dxfattribs={"layer": "TEXT", "height": 3.5}).set_placement((calc["tip_diameter_mm"] / 2.0 + 5, calc["tip_diameter_mm"] / 2.0 + 5))
    doc.saveas(path)


def build_gear_files(args: dict, out_dir: Path, freecad_executable: Path) -> dict:
    params = _params(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "gear.py"
    fcstd = out_dir / "gear.FCStd"
    step = out_dir / "gear.step"
    svg = out_dir / "gear.svg"
    pdf = out_dir / "gear.pdf"
    dxf = out_dir / "gear.dxf"
    data = out_dir / "gear.json"
    script.write_text(_freecad_script(params, fcstd, step), encoding="utf-8")
    completed = subprocess.run([str(freecad_executable), str(script)], capture_output=True, timeout=300)
    if completed.returncode != 0:
        raise RuntimeError("FreeCAD 齿轮建模失败：" + completed.stderr.decode("utf-8", "replace")[-800:])
    if not fcstd.is_file() or not step.is_file():
        raise RuntimeError("FreeCAD 未生成齿轮 FCStd/STEP")
    W, H, prims = _drawing(params)
    svg.write_text(_svg(W, H, prims), encoding="utf-8")
    _pdf(W, H, prims, pdf)
    _dxf(params, dxf)
    data.write_text(json.dumps({k: v for k, v in params.items() if k != "points"} |
                              {"points_count": len(params["points"])}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"params": params, "calculation": params["calculation"], "script": script,
            "fcstd": fcstd, "step": step, "drawing_svg": svg, "drawing_pdf": pdf,
            "dxf": dxf, "bom": data}
