"""Render and register a complete, offline-replayable chart export."""
from __future__ import annotations

from functools import lru_cache
import hashlib
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from PIL import Image
from pypdf import PdfReader

from app.charting import FitChart, ScanChart, VEGA_LITE_VERSION, fit_chart, make_spec, scan_chart, table_chart
from app.db import connection, uid
from app.documents import artifact_record, safe_name


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


@lru_cache(maxsize=1)
def fonts():
    from matplotlib import font_manager

    names = ["Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans"]
    paths = []
    if os.name == "nt":
        paths.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc")
    for name in names:
        try:
            paths.append(Path(font_manager.findfont(name, fallback_to_default=False)))
        except ValueError:
            continue
    return tuple(dict.fromkeys(path for path in paths if path.is_file()))


def figure_texts(spec):
    """Collect user-visible text and nominal values, excluding source metadata."""
    def visit(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if key == "datasets":
                    for dataset in item.values():
                        for row in dataset:
                            for field in ("group", "status"):
                                if isinstance(row.get(field), str):
                                    yield row[field]
                            if isinstance(row.get("x"), str):
                                yield row["x"]
                elif key in {"title", "text", "subtitle"} and isinstance(item, (str, list)):
                    yield from ([item] if isinstance(item, str) else item)
                else:
                    yield from visit(item)
        elif isinstance(node, list):
            for item in node:
                yield from visit(item)
    return visit(spec)


def figure_font(spec):
    from matplotlib.ft2font import FT2Font

    remaining = {ord(char) for label in figure_texts(spec) for char in label if not char.isspace()}
    faces = []
    for path in fonts():
        font = FT2Font(str(path))
        covered = {code for code in remaining if font.get_char_index(code)}
        if not covered:
            continue
        register_font_folder(path.parent)
        faces.append({"family": font.family_name, "file": path.name,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        remaining -= covered
        if not remaining:
            return {"family": ", ".join(face["family"] for face in faces), "faces": faces}
    missing = "".join(chr(code) for code in sorted(remaining)[:12])
    raise ValueError(f"本机字体缺少图表字符 {missing}，请补充支持这些字符的字体")


@lru_cache(maxsize=16)
def register_font_folder(folder):
    # System fonts are already available to vl-convert. Register extra paths once.
    if os.name == "nt" and folder == Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts":
        return
    import vl_convert
    vl_convert.register_font_directory(str(folder))


def pdf_font_names(content):
    page = PdfReader(io.BytesIO(content)).pages[0]
    fonts = page["/Resources"].get("/Font")
    if fonts is None:
        return []
    base_fonts = [font.get_object().get("/BaseFont") for font in fonts.get_object().values()]
    return sorted({str(name).lstrip("/").split("+", 1)[-1] for name in base_fonts if name})


def render(spec, requested, *, vega_lite_version=None):
    import vl_convert

    if vega_lite_version is None:
        vega_lite_version = "5.21" if spec.get("$schema", "").endswith("/v5.json") else VEGA_LITE_VERSION
    options = {"vl_version": vega_lite_version, "allowed_base_urls": []}
    functions = {"svg": vl_convert.vegalite_to_svg, "pdf": vl_convert.vegalite_to_pdf,
                 "png": vl_convert.vegalite_to_png}
    rendered = {}
    for kind in requested:
        try:
            output = functions[kind](spec, **options)
            output = output.encode("utf-8") if isinstance(output, str) else output
            if kind == "svg" and ET.fromstring(output).tag != "{http://www.w3.org/2000/svg}svg":
                raise ValueError("无有效 SVG 根节点")
            if kind == "pdf" and (not output.startswith(b"%PDF-") or len(PdfReader(io.BytesIO(output)).pages) != 1):
                raise ValueError("预期单页 PDF")
            if kind == "png":
                with Image.open(io.BytesIO(output)) as image:
                    if image.format != "PNG" or min(image.size) <= 0:
                        raise ValueError("无有效 PNG")
                    image.verify()
        except Exception as exc:
            raise RuntimeError(f"{kind.upper()} 绘制或校验失败：{exc}") from exc
        rendered[kind] = output
    return rendered


def export_chart(project_id, request, artifacts_root, *, study_path=None):
    if isinstance(request, ScanChart):
        body, data, source, notes = scan_chart(project_id, request, study_path)
    elif isinstance(request, FitChart):
        body, data, source, notes = fit_chart(project_id, request)
    else:
        body, data, source, notes = table_chart(project_id, request)
    try:
        import vl_convert
    except ImportError as exc:
        raise RuntimeError("缺少图表渲染器，请按 ChemELLM 的锁定依赖更新插件环境") from exc
    vega_lite_version = request.vega_lite_version
    if vega_lite_version not in vl_convert.get_vegalite_versions():
        raise RuntimeError(f"渲染器缺少 Vega-Lite {vega_lite_version}，请检查锁定依赖")
    spec = make_spec(request.title, body, data, notes, "", vega_lite_version)
    font = figure_font(spec)
    spec["config"]["font"] = font["family"]
    renderer = {"vl_convert_python": version("vl-convert-python"), "vega_lite": vega_lite_version, "font": font}
    files = {"chart.vl.json": json_bytes(spec), "chart-data.json": json_bytes(data)}
    for kind, output in render(spec, list(dict.fromkeys([*request.formats, "png"])),
                               vega_lite_version=vega_lite_version).items():
        files[f"chart.{kind}"] = output
    if "chart.pdf" in files:
        renderer["pdf_fonts"] = pdf_font_names(files["chart.pdf"])
    manifest = {"schema_version": 1, "mode": request.mode, "request": request.model_dump(mode="json"),
                "source": source, "notes": notes, "renderer": renderer,
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    files["chart-source.json"] = json_bytes(manifest)

    folder = artifacts_root / uid()
    folder.mkdir()
    try:
        for name, content in files.items():
            (folder / name).write_bytes(content)
        companions = ["chart.vl.json", "chart-data.json", "chart-source.json"]
        if "png" not in request.formats:
            companions.append("chart.png")
        # The database transaction publishes the complete set or none of it.
        with connection() as db:
            artifacts = []
            for name in [*(f"chart.{kind}" for kind in request.formats), *companions]:
                kind = Path(name).suffix[1:]
                primary = name not in companions
                display_name = f"{safe_name(request.title)}.{kind}" if primary else name
                artifacts.append(artifact_record(project_id, folder / name, name=display_name, kind=kind,
                    preview=folder / "chart.png" if primary else None, db=db, metadata={
                        "source": "kernel export_chart", "chart_group": folder.name, "relative_path": name,
                        "delivery_role": "deliverable" if primary else "resource",
                        "preview_mode": "visual" if primary else None,
                        "depends_on": companions if primary else [],
                    }))
    except Exception:
        shutil.rmtree(folder)
        raise
    return {"engine": "Vega-Lite", "version": renderer["vl_convert_python"], "renderer": renderer,
            "mode": request.mode, "notes": notes, "artifacts": artifacts}
