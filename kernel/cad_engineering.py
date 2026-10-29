"""Engineering equipment drawing + thickness calculation.

The solid is still a simplified nominal-geometry model (no weld details), but
the drawing carries the engineering data that a real equipment drawing needs:

- shell / head / nozzle thickness calculated to GB/T 150.1-2024 (internal
  pressure, simplified formula) with material, allowable stress, weld
  efficiency and corrosion allowance recorded;
- A3 front elevation with overall/tangent dimensions, nozzle elevation marks,
  centre line and nozzle schedule;
- design-data notes, bill of materials and title block.

Nozzle flanges are added to the FreeCAD solid so the STEP/FCStd model looks
like a real vessel outline; the shell is not modelled hollow (wall thickness is
calculated and annotated, not cut).
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from pid_sheet import _pdf, _svg, _text_width

PLATE_SERIES = [6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0, 34.0, 36.0, 38.0, 40.0, 44.0, 48.0, 50.0]
DEFAULT_DESIGN = {
    "design_pressure_mpa": 1.0,
    "design_temperature_c": 100.0,
    "material": "Q345R",
    "allowable_stress_mpa": 189.0,
    "joint_efficiency": 1.0,
    "corrosion_allowance_mm": 2.0,
    "mill_tolerance_mm": 0.3,
    "head_type": "2:1 椭圆封头",
}
CAD_DEFAULTS = {
    "vessel": {"diameter_mm": 3000.0, "tangent_length_mm": 8000.0, "skirt_height_mm": 2000.0},
    "column": {"diameter_mm": 2400.0, "tangent_length_mm": 20000.0, "skirt_height_mm": 3000.0},
    "tank": {"diameter_mm": 8000.0, "tangent_length_mm": 9000.0, "skirt_height_mm": 0.0},
    "exchanger": {"diameter_mm": 1200.0, "tangent_length_mm": 5000.0, "skirt_height_mm": 0.0},
}


def _params(args: dict) -> dict:
    equipment_type = str(args.get("equipment_type") or "vessel").lower()
    if equipment_type not in CAD_DEFAULTS:
        raise ValueError("equipment_type 必须是 vessel/column/tank/exchanger")
    defaults = CAD_DEFAULTS[equipment_type]
    diameter = float(args.get("diameter_mm", defaults["diameter_mm"]))
    length = float(args.get("tangent_length_mm", defaults["tangent_length_mm"]))
    skirt = float(args.get("skirt_height_mm", defaults["skirt_height_mm"]))
    head_height = float(args.get("head_height_mm", diameter / 4.0))
    if not (200.0 <= diameter <= 12000.0):
        raise ValueError("diameter_mm 超出 200~12000 mm")
    if not (300.0 <= length <= 80000.0):
        raise ValueError("tangent_length_mm 超出 300~80000 mm")
    if not (0.0 <= skirt <= 30000.0):
        raise ValueError("skirt_height_mm 超出 0~30000 mm")
    if equipment_type in {"vessel", "column"} and not (diameter * 0.1 <= head_height <= diameter * 0.6):
        raise ValueError("head_height_mm 应在 0.1D~0.6D 之间")
    nozzles = args.get("nozzles") or []
    if not isinstance(nozzles, list):
        raise ValueError("nozzles 必须是数组")
    clean = []
    for index, nozzle in enumerate(nozzles):
        dn = float(nozzle.get("dn", 100.0))
        if not (10.0 <= dn <= diameter * 0.5):
            raise ValueError(f"nozzle {index}: dn 超出 10~D/2 范围")
        position = str(nozzle.get("position") or "top").lower()
        if position not in {"top", "bottom", "side"}:
            raise ValueError(f"nozzle {index}: position 必须是 top/bottom/side")
        clean.append({
            "tag": str(nozzle.get("tag") or f"N{index + 1}"),
            "dn": dn,
            "position": position,
            "offset_mm": float(nozzle.get("offset_mm", 0.0)),
            "azimuth_deg": float(nozzle.get("azimuth_deg", 0.0)),
            "elevation_mm": float(nozzle.get("elevation_mm", length / 2.0)),
            "flange": str(nozzle.get("flange") or "WN PN16 RF"),
        })
    design = dict(DEFAULT_DESIGN)
    for key in DEFAULT_DESIGN:
        if key in args and args[key] is not None:
            design[key] = args[key]
    design["design_pressure_mpa"] = float(design["design_pressure_mpa"])
    design["design_temperature_c"] = float(design["design_temperature_c"])
    design["allowable_stress_mpa"] = float(design["allowable_stress_mpa"])
    design["joint_efficiency"] = float(design["joint_efficiency"])
    design["corrosion_allowance_mm"] = float(design["corrosion_allowance_mm"])
    design["mill_tolerance_mm"] = float(design["mill_tolerance_mm"])
    if not (0.0 < design["design_pressure_mpa"] <= 35.0):
        raise ValueError("design_pressure_mpa 应在 0~35 MPa（GB/T 150.1 范围）")
    if not (0.0 < design["joint_efficiency"] <= 1.0):
        raise ValueError("joint_efficiency 应在 0~1")
    return {
        "equipment_type": equipment_type,
        "tag": str(args.get("tag") or "EQ-001"),
        "diameter_mm": diameter,
        "tangent_length_mm": length,
        "skirt_height_mm": skirt,
        "head_height_mm": head_height,
        "nozzles": clean,
        "design": design,
    }


def _round_plate(value: float) -> float:
    for plate in PLATE_SERIES:
        if plate >= value - 1e-9:
            return plate
    return PLATE_SERIES[-1]


def _design_calc(params: dict) -> dict:
    design = params["design"]
    pressure = design["design_pressure_mpa"]
    stress = design["allowable_stress_mpa"]
    phi = design["joint_efficiency"]
    c1 = design["mill_tolerance_mm"]
    c2 = design["corrosion_allowance_mm"]
    diameter = params["diameter_mm"]
    head_ratio = 2.0 if abs(params["head_height_mm"] - diameter / 4.0) < 1e-6 else diameter / (4.0 * params["head_height_mm"])
    if params["equipment_type"] == "tank":
        shell_required = 6.0
        head_required = 6.0
    else:
        shell_required = pressure * diameter / (2.0 * stress * phi - pressure) + c1 + c2
        head_required = pressure * diameter / (2.0 * stress * phi - 0.5 * pressure) + c1 + c2
    shell = _round_plate(shell_required)
    head = _round_plate(head_required)
    nozzle_rows = []
    for nozzle in params["nozzles"]:
        dn = nozzle["dn"]
        required = pressure * dn / (2.0 * stress * phi - pressure) + c1 + c2
        nozzle_rows.append({**nozzle, "required_mm": round(required, 2), "selected_mm": _round_plate(required)})
    skirt = max(8.0, _round_plate(0.006 * diameter + 6.0)) if params["skirt_height_mm"] > 0 else 0.0

    density = 7850e-9  # kg/mm^3
    outer = diameter + 2.0 * shell
    shell_volume = math.pi / 4.0 * (outer ** 2 - diameter ** 2) * params["tangent_length_mm"]
    head_area = 1.15 * math.pi * (diameter / 2.0) ** 2
    head_volume = head_area * head * 2.0
    nozzle_volume = 0.0
    for row in nozzle_rows:
        length = max(250.0, row["dn"] * 2.0)
        nozzle_volume += math.pi * row["dn"] * row["selected_mm"] * length
        nozzle_volume += math.pi / 4.0 * (1.6 * row["dn"]) ** 2 * 20.0
    skirt_volume = 0.0
    if params["skirt_height_mm"] > 0:
        skirt_volume = math.pi / 4.0 * (outer ** 2 - (outer - skirt) ** 2) * params["skirt_height_mm"]
    total_volume = shell_volume + head_volume + nozzle_volume + skirt_volume
    weights = {
        "shell_kg": round(shell_volume * density, 1),
        "heads_kg": round(head_volume * density, 1),
        "nozzles_kg": round(nozzle_volume * density, 1),
        "skirt_kg": round(skirt_volume * density, 1),
        "total_kg": round(total_volume * density, 1),
    }
    return {
        "formula": "GB/T 150.1-2024 内压圆筒/椭圆封头简化式：δ=P·Di/(2[σ]tφ−P)+C1+C2（封头 0.5P）",
        "shell_required_mm": round(shell_required, 2),
        "shell_selected_mm": shell,
        "head_required_mm": round(head_required, 2),
        "head_selected_mm": head,
        "skirt_selected_mm": skirt,
        "head_ratio": round(head_ratio, 2),
        "nozzles": nozzle_rows,
        "weights": weights,
    }


def _freecad_script(params: dict, calc: dict, fcstd: Path, step: Path) -> str:
    return f'''
import math
import FreeCAD
import Part

P = {json.dumps(params, ensure_ascii=False)}
C = {json.dumps(calc["weights"], ensure_ascii=False)}
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
        shapes.append(Part.makeCylinder(D / 2.0 + 100.0, SKIRT + 20.0, FreeCAD.Vector(0, 0, -SKIRT), FreeCAD.Vector(0, 0, 1)))
elif KIND == "tank":
    shapes.append(Part.makeCylinder(D / 2.0, L, FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1)))
    shapes.append(Part.makeCylinder(D / 2.0, 20.0, FreeCAD.Vector(0, 0, -20.0), FreeCAD.Vector(0, 0, 1)))
    shapes.append(Part.makeCone(D / 2.0, 8.0, max(200.0, D * 0.15), FreeCAD.Vector(0, 0, L), FreeCAD.Vector(0, 0, 1)))
elif KIND == "exchanger":
    shapes.append(Part.makeCylinder(D / 2.0, L, FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0)))
    for x, sign in ((0.0, -1.0), (L, 1.0)):
        head = head_solid(D / 2.0, H, 0.0)
        head.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 1, 0), -90.0 if sign > 0 else 90.0)
        head.translate(FreeCAD.Vector(x, 0, 0))
        shapes.append(head)
    if SKIRT > 0:
        for x in (L * 0.25, L * 0.75):
            shapes.append(Part.makeBox(D * 0.6, D * 1.2, SKIRT, FreeCAD.Vector(x - D * 0.3, -D * 0.6, -SKIRT - D / 2.0)))

nozzle_shapes = []
for nozzle in P["nozzles"]:
    r = nozzle["dn"] / 2.0
    length = max(250.0, nozzle["dn"] * 2.0)
    if KIND == "exchanger":
        continue
    angle = math.radians(nozzle["azimuth_deg"])
    if nozzle["position"] == "top":
        base = FreeCAD.Vector(nozzle["offset_mm"], 0, L + H - 30.0)
        direction = FreeCAD.Vector(0, 0, 1)
    elif nozzle["position"] == "bottom":
        base = FreeCAD.Vector(nozzle["offset_mm"], 0, -H + 30.0)
        direction = FreeCAD.Vector(0, 0, -1)
    else:
        base = FreeCAD.Vector((D / 2.0 - 20.0) * math.cos(angle), (D / 2.0 - 20.0) * math.sin(angle), nozzle["elevation_mm"])
        direction = FreeCAD.Vector(math.cos(angle), math.sin(angle), 0)
    pipe = Part.makeCylinder(r, length, base, direction)
    flange = Part.makeCylinder(r * 1.6, 20.0, base + direction * (length - 20.0), direction)
    nozzle_shapes.extend([pipe, flange])

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


def _arrow_prims(x1, y1, x2, y2, color="ink"):
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 2.0
    left = (x2 - size * math.cos(angle - 0.45), y2 - size * math.sin(angle - 0.45))
    right = (x2 - size * math.cos(angle + 0.45), y2 - size * math.sin(angle + 0.45))
    return [("polygon", [left, right, (x2, y2)], color, color, 0.2)]


def _drawing(params: dict, calc: dict) -> tuple[float, float, list[tuple]]:
    W, H = 420.0, 297.0
    ink = "#1f3b57"
    muted = "#5a6b7d"
    grid = "#90a4ae"
    paper = "#ffffff"
    prims: list[tuple] = [("rect", 0, 0, W, H, paper, None, 0.0, 0.0),
                          ("rect", 25, 10, W - 35, H - 20, None, ink, 0.6, 0.0)]
    kind = params["equipment_type"]
    tag = params["tag"]
    diameter = params["diameter_mm"]
    length = params["tangent_length_mm"]
    head = params["head_height_mm"]
    skirt = params["skirt_height_mm"]
    design = params["design"]

    # view geometry
    total_h = (length + 2.0 * head + skirt) if kind != "exchanger" else diameter
    total_w = (diameter + 2.0 * head) if kind == "exchanger" else diameter
    view_area = (40.0, 55.0, 250.0, 232.0)
    raw_scale = min((view_area[2] - view_area[0]) / total_w, (view_area[3] - view_area[1]) / total_h)
    scale = 0.01
    for denominator in (10, 20, 25, 50, 100, 200, 500):
        if 1.0 / denominator <= raw_scale:
            scale = 1.0 / denominator
            break
    cx = (view_area[0] + view_area[2]) / 2.0
    top = view_area[1] + ((view_area[3] - view_area[1]) - total_h * scale) / 2.0
    bottom = top + total_h * scale
    prims.append(("text", 40, 45, f"{tag}  {'塔器' if kind == 'column' else ('储罐' if kind == 'tank' else ('换热器' if kind == 'exchanger' else '容器'))}  (立面)", 4.0, ink, "start", True))
    prims.append(("line", cx, top - 6, cx, bottom + 6, muted, 0.25, "dashdot"))

    if kind == "exchanger":
        prims.append(("rect", cx - diameter * scale / 2, top + head * scale, diameter * scale, length * scale, "#eef4fb", ink, 0.5, 1.0))
        for sign in (-1, 1):
            x = cx + sign * (diameter * scale / 2)
            prims.append(("ellipse", x, top + head * scale + length * scale / 2, head * scale, diameter * scale / 2,
                          "#eef4fb", ink, 0.5))
        prims.append(("text", cx, bottom + 10, f"L={length:.0f}", 2.8, muted, "middle", False))
    else:
        shell_top = top + head * scale
        shell_bottom = shell_top + length * scale
        if kind == "tank":
            shell_top = top
            shell_bottom = top + length * scale
        prims.append(("rect", cx - diameter * scale / 2, shell_top, diameter * scale, length * scale, "#eef4fb", ink, 0.5, 1.0))
        if kind != "tank":
            def head_points(base_y: float, up: bool) -> list[tuple]:
                points = [(cx - diameter * scale / 2, base_y)]
                for step in range(1, 12):
                    angle = math.pi * step / 12.0
                    points.append((cx - diameter * scale / 2 * math.cos(angle),
                                   base_y - head * scale * math.sin(angle) if up else base_y + head * scale * math.sin(angle)))
                points.append((cx + diameter * scale / 2, base_y))
                return points
            prims.append(("polygon", head_points(shell_top, True), "#eef4fb", ink, 0.5))
            prims.append(("polygon", head_points(shell_bottom, False), "#eef4fb", ink, 0.5))
        if skirt > 0:
            prims.append(("rect", cx - diameter * scale * 0.45, shell_bottom + (head * scale if kind != "tank" else 0),
                          diameter * scale * 0.9, skirt * scale, "#d9e6f2", ink, 0.4, 0.0))
        # nozzles
        for nozzle in params["nozzles"]:
            dn = nozzle["dn"]
            if nozzle["position"] == "top":
                nx = cx + nozzle["offset_mm"] * scale
                prims.append(("rect", nx - dn * scale / 2, top - head * scale - 6, max(dn * scale, 3), 8, "#cfe3f5", ink, 0.3, 0.0))
                prims.append(("text", nx, top - head * scale - 9, nozzle["tag"], 2.2, ink, "middle", False))
            elif nozzle["position"] == "bottom":
                nx = cx + nozzle["offset_mm"] * scale
                prims.append(("rect", nx - dn * scale / 2, bottom + 6, max(dn * scale, 3), 8, "#cfe3f5", ink, 0.3, 0.0))
                prims.append(("text", nx, bottom + 20, nozzle["tag"], 2.2, ink, "middle", False))
            else:
                ny = shell_top + nozzle["elevation_mm"] * scale
                prims.append(("rect", cx + diameter * scale / 2 - 2, ny - max(dn * scale, 3) / 2, 8, max(dn * scale, 3), "#cfe3f5", ink, 0.3, 0.0))
                prims.append(("text", cx + diameter * scale / 2 + 10, ny, nozzle["tag"], 2.2, ink, "start", False))

        # dimensions
        dim_x = 35.0
        prims.append(("line", dim_x, top, dim_x, bottom, muted, 0.25, None))
        prims.extend(_arrow_prims(dim_x, top, dim_x, bottom, muted))
        prims.extend(_arrow_prims(dim_x, bottom, dim_x, top, muted))
        prims.append(("text", dim_x - 3, (top + bottom) / 2, f"{total_h:.0f}", 2.8, muted, "end", False))
        tx = cx + diameter * scale / 2 + 28
        prims.append(("line", tx, shell_top, tx, shell_bottom, muted, 0.25, None))
        prims.extend(_arrow_prims(tx, shell_top, tx, shell_bottom, muted))
        prims.extend(_arrow_prims(tx, shell_bottom, tx, shell_top, muted))
        prims.append(("text", tx + 3, (shell_top + shell_bottom) / 2, f"L={length:.0f}", 2.8, muted, "start", False))
        prims.append(("text", cx, top - head * scale - 16, f"φ{diameter:.0f}", 2.8, muted, "middle", False))

    # design data & notes (right column)
    x0 = 262.0
    y = 55.0
    prims.append(("text", x0, y - 3, "设计数据（GB/T 150.1-2024 简化计算）", 3.2, ink, "start", True))
    rows = [
        ("设计压力", f"{design['design_pressure_mpa']:.2f} MPa"),
        ("设计温度", f"{design['design_temperature_c']:.0f} ℃"),
        ("材料", design["material"]),
        ("许用应力 [σ]t", f"{design['allowable_stress_mpa']:.0f} MPa"),
        ("焊接接头系数 φ", f"{design['joint_efficiency']:.2f}"),
        ("腐蚀裕量 C2", f"{design['corrosion_allowance_mm']:.1f} mm"),
        ("钢板负偏差 C1", f"{design['mill_tolerance_mm']:.1f} mm"),
        ("筒体计算/选取", f"{calc['shell_required_mm']:.2f} / {calc['shell_selected_mm']:.0f} mm"),
        ("封头计算/选取", f"{calc['head_required_mm']:.2f} / {calc['head_selected_mm']:.0f} mm"),
        ("设备净重（估）", f"{calc['weights']['total_kg']:.0f} kg"),
    ]
    for index, (key, value) in enumerate(rows):
        yy = y + 8 + index * 8.5
        prims.append(("text", x0, yy, key, 2.7, muted, "start", False))
        prims.append(("text", x0 + 42, yy, value, 2.7, ink, "start", False))

    # nozzle schedule
    ny0 = y + 100
    prims.append(("text", x0, ny0 - 3, "接管表", 3.2, ink, "start", True))
    headers = ["位号", "DN", "方位/标高", "法兰"]
    widths = [18, 14, 42, 40]
    rows_n = []
    for nozzle in params["nozzles"]:
        where = {"top": "顶部", "bottom": "底部"}.get(nozzle["position"], "")
        if nozzle["position"] == "side":
            where = f"{nozzle['azimuth_deg']:.0f}° / {nozzle['elevation_mm']:.0f}"
        rows_n.append([nozzle["tag"], f"{nozzle['dn']:.0f}", where, nozzle["flange"]])
    yy = ny0
    prims.append(("rect", x0, yy, sum(widths), 8, "#dce8f4", ink, 0.35, 0.0))
    cx0 = x0
    for header, width in zip(headers, widths):
        prims.append(("text", cx0 + 1, yy + 5.5, header, 2.5, ink, "start", True))
        cx0 += width
    yy += 8
    for row in rows_n:
        prims.append(("rect", x0, yy, sum(widths), 7.5, None, grid, 0.3, 0.0))
        cx0 = x0
        for value, width in zip(row, widths):
            prims.append(("text", cx0 + 1, yy + 5.2, str(value), 2.5, ink, "start", False))
            cx0 += width
        yy += 7.5
    prims.append(("rect", x0, ny0, sum(widths), yy - ny0, None, ink, 0.4, 0.0))

    # design notes
    notes = [
        "注：1. 本图为参数化生成的设备外形与设计数据，不含焊缝详图、开孔补强与制造公差。",
        "2. 壁厚按 GB/T 150.1-2024 内压简化式计算，取钢板系列值；封头为 2:1 椭圆封头。",
        "3. 实体模型为名义外形（未抽壳），厚度以本图设计数据为准；制造前须按正式标准复核。",
        "4. 设备净重为估算值（材料密度 7850 kg/m³），不含保温、内件、平台与梯子。",
    ]
    for index, note in enumerate(notes):
        prims.append(("text", 40, 252 + index * 5.2, note, 2.2, muted, "start", False))

    # title block
    tx, ty = 230.0, 245.0
    prims.append(("rect", tx, ty, 165, 42, None, ink, 0.6, 0.0))
    prims.append(("line", tx, ty + 16, tx + 165, ty + 16, ink, 0.4, None))
    prims.append(("line", tx, ty + 28, tx + 165, ty + 28, ink, 0.4, None))
    prims.append(("text", tx + 3, ty + 10, f"{tag} 设备图", 4.2, ink, "start", True))
    prims.append(("text", tx + 3, ty + 22, f"图号 DWG-{tag}-01    版本 A", 2.5, muted, "start", False))
    prims.append(("text", tx + 3, ty + 31, "设计 ChemELLM    校核 待校核", 2.4, ink, "start", False))
    prims.append(("text", tx + 3, ty + 39, f"比例 1:{max(1, round(1.0 / scale))}    单位 mm    日期 2026-10-22", 2.2, muted, "start", False))
    prims.append(("text", tx + 105, ty + 22, f"净重 {calc['weights']['total_kg']:.0f} kg", 2.5, ink, "start", False))
    prims.append(("text", tx + 105, ty + 31, f"筒体 δ={calc['shell_selected_mm']:.0f}", 2.4, ink, "start", False))
    return W, H, prims


def build_cad_files(args: dict, out_dir: Path, freecad_executable: Path) -> dict:
    params = _params(args)
    calc = _design_calc(params)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "part.py"
    fcstd = out_dir / "part.FCStd"
    step = out_dir / "part.step"
    svg = out_dir / "equipment.svg"
    pdf = out_dir / "equipment.pdf"
    bom = out_dir / "bom.json"
    script.write_text(_freecad_script(params, calc, fcstd, step), encoding="utf-8")
    completed = subprocess.run([str(freecad_executable), str(script)], capture_output=True, timeout=300)
    if completed.returncode != 0:
        raise RuntimeError("FreeCAD 建模失败：" + completed.stderr.decode("utf-8", "replace")[-800:])
    if not fcstd.is_file() or not step.is_file():
        raise RuntimeError("FreeCAD 未生成 FCStd/STEP")
    W, H, prims = _drawing(params, calc)
    svg.write_text(_svg(W, H, prims), encoding="utf-8")
    _pdf(W, H, prims, pdf)
    bom.write_text(json.dumps({"tag": params["tag"], "design": params["design"], "calculation": calc,
                               "nozzles": params["nozzles"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"params": params, "calculation": calc, "script": script, "fcstd": fcstd,
            "step": step, "drawing_svg": svg, "drawing_pdf": pdf, "bom": bom}
