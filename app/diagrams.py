"""Editable topology and engineering sketches; no compliance claims."""
import html
import json
import math
from typing import Literal
from pydantic import Field, model_validator
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from app.calculations import Inputs
from app.config import ARTIFACTS
from app.db import uid
from app.documents import artifact_record, safe_name

class Node(Inputs):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,24}$")
    label: str = Field(min_length=1, max_length=60)
    kind: Literal["reactor", "column", "exchanger", "pump", "tank", "instrument"] = "tank"
    x: float = Field(ge=60, le=1500)
    y: float = Field(ge=110, le=900)

class Edge(Inputs):
    source: str
    target: str
    label: str = Field(default="", max_length=60)
    route: list[tuple[float,float]] | None = Field(default=None,min_length=2,max_length=16,
        description="可选：含起终点的折线坐标，用于人工核对的避让布线；不提供时自动布局")

class DiagramInput(Inputs):
    title: str = Field(default="工艺流程草图", min_length=1, max_length=100)
    drawing_type: Literal["PFD", "P&ID"] = "PFD"
    nodes: list[Node] = Field(min_length=1, max_length=40)
    edges: list[Edge] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def topology(self):
        ids=[n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("设备位号必须唯一")
        if any(e.source not in ids or e.target not in ids or e.source==e.target for e in self.edges):
            raise ValueError("物流必须连接两个不同的已有设备")
        if any(not 0<=x<=1700 or not 90<=y<=1060 for e in self.edges for x,y in (e.route or [])):
            raise ValueError("自定义物流坐标超出图纸范围")
        return self

DEMO={"title":"乙苯脱氢制苯乙烯 · 概念流程", "drawing_type":"PFD", "nodes":[
    {"id":"TK-101","label":"乙苯原料","kind":"tank","x":100,"y":230},
    {"id":"E-101","label":"原料预热","kind":"exchanger","x":310,"y":230},
    {"id":"R-101","label":"脱氢反应器","kind":"reactor","x":520,"y":230},
    {"id":"E-102","label":"余热回收","kind":"exchanger","x":730,"y":230},
    {"id":"T-101","label":"分离精制","kind":"column","x":940,"y":230},
    {"id":"TK-102","label":"苯乙烯产品","kind":"tank","x":1150,"y":230},
    {"id":"V-101","label":"氢气副产物","kind":"tank","x":730,"y":470}],
    "edges":[{"source":"TK-101","target":"E-101","label":"S01 乙苯"},{"source":"E-101","target":"R-101","label":"S02"},{"source":"R-101","target":"E-102","label":"S03 反应物流"},{"source":"E-102","target":"T-101","label":"S04"},{"source":"T-101","target":"TK-102","label":"S05 产品"},{"source":"E-102","target":"V-101","label":"S06 含氢气"},{"source":"T-101","target":"E-101","label":"S07 循环乙苯"}]}

def draw(project_id,p):
    folder=ARTIFACTS/uid()
    folder.mkdir()
    width=max([n.x for n in p.nodes]+[x for e in p.edges for x,y in (e.route or [])])+160
    height=max([n.y for n in p.nodes]+[y for e in p.edges for x,y in (e.route or [])])+160
    lookup={n.id:n for n in p.nodes}
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"><defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#146b61"/></marker></defs><rect width="100%" height="100%" fill="#f7faf8"/><g font-family="sans-serif" fill="#163e38"><text x="35" y="42" font-size="23">{html.escape(p.title)}</text><text x="35" y="72" font-size="13">{html.escape(p.drawing_type)} · 概念草图 / 未完成工艺、安全和制图规范审核</text>']
    svg.insert(1,'<metadata id="chemellm-topology">'+html.escape(p.model_dump_json(exclude_none=True))+'</metadata>')
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    pdf_path=folder/(safe_name(p.title)+".pdf")
    pdf=canvas.Canvas(str(pdf_path),pagesize=(width,height))
    pdf.setFont("STSong-Light",20)
    pdf.drawString(35,height-42,p.title)
    pdf.setFont("STSong-Light",12)
    pdf.drawString(35,height-72,"概念草图：未完成工艺、安全和制图规范审核")
    segments=[]
    for i,e in enumerate(p.edges):
        a,b=lookup[e.source],lookup[e.target]
        if e.route:
            points=e.route
        elif b.x>a.x and abs(b.y-a.y)<80:
            points=[(a.x+50,a.y),(b.x-50,b.y)]
        elif abs(b.x-a.x)<80 and b.y>a.y:
            points=[(a.x,a.y+60),(b.x,b.y-60)]
        else:
            route=max(a.y,b.y)+100+12*(i%3)
            points=[(a.x,a.y+60),(a.x,route),(b.x,route),(b.x,b.y+60)]
        coords=" ".join(f"{x},{y}" for x,y in points)
        svg.append(f'<polyline points="{coords}" fill="none" stroke="#146b61" stroke-width="2" marker-end="url(#a)"/>')
        lx=(points[0][0]+points[1][0])/2
        ly=(points[0][1]+points[1][1])/2-9
        svg.append(f'<text x="{lx}" y="{ly}" text-anchor="middle" font-size="12">{html.escape(e.label)}</text>')
        pdf.setStrokeColorRGB(.08,.42,.38)
        for start,end in zip(points,points[1:]):
            pdf.line(start[0],height-start[1],end[0],height-end[1])
        # Arrowhead at the last segment in the PDF too.
        x,y=points[-1]; px,py=points[-2]
        theta=math.atan2(y-py,x-px)
        for offset in (-.45,.45):
            pdf.line(x,height-y,x-10*math.cos(theta+offset),height-(y-10*math.sin(theta+offset)))
        pdf.drawCentredString(lx,height-ly,e.label)
        segments.append(points)
    for n in p.nodes:
        x,y=n.x,n.y
        if n.kind in {"pump","exchanger","instrument"}:
            svg.append(f'<circle cx="{x}" cy="{y}" r="40" fill="#fff" stroke="#146b61" stroke-width="2"/>')
            pdf.circle(x,height-y,40)
            if n.kind=="exchanger":
                svg.append(f'<path d="M{x-27} {y+20} L{x+27} {y-20}" stroke="#146b61" stroke-width="2"/>')
                pdf.line(x-27,height-y-20,x+27,height-y+20)
        else:
            svg.append(f'<rect x="{x-48}" y="{y-60}" width="96" height="120" rx="24" fill="#fff" stroke="#146b61" stroke-width="2"/>')
            pdf.roundRect(x-48,height-y-60,96,120,24)
        svg.append(f'<text x="{x}" y="{y-5}" text-anchor="middle" font-size="14" font-weight="bold">{html.escape(n.id)}</text><text x="{x}" y="{y+18}" text-anchor="middle" font-size="12">{html.escape(n.label)}</text>')
        pdf.drawCentredString(x,height-y+5,n.id)
        pdf.drawCentredString(x,height-y-18,n.label)
    svg.append('</g></svg>')
    svg_path=folder/(safe_name(p.title)+".svg")
    svg_path.write_text("".join(svg),encoding="utf-8")
    pdf.save()
    topology=folder/(safe_name(p.title)+".json")
    topology.write_text(p.model_dump_json(indent=2,exclude_none=True),encoding="utf-8")
    metadata={"status":"draft","drawing_type":p.drawing_type,"warnings":["尚不满足完整工程 PFD/P&ID 及竞赛制图要求；需要尺寸、仪表、安全联锁、物流表和规范审核。"]}
    pending=[{"path":path,"text":p.model_dump_json(exclude_none=True),"metadata":metadata} for path in (svg_path,pdf_path,topology)]
    try:
        import ezdxf
        from ezdxf.enums import TextEntityAlignment
        doc=ezdxf.new("R2004")
        doc.styles.new("GB",dxfattribs={"font":"txt.shx","bigfont":"gbcbig.shx"})
        space=doc.modelspace()
        space.add_text(p.title,dxfattribs={"height":23,"style":"GB","insert":(35,-42)})
        space.add_text(p.drawing_type+" 概念草图 / 未完成工艺、安全和制图规范审核",dxfattribs={"height":13,"style":"GB","insert":(35,-72)})
        for n in p.nodes:
            x,y=n.x,-n.y
            if n.kind in {"pump","exchanger","instrument"}:
                space.add_circle((x,y),40)
                if n.kind=="exchanger":space.add_line((x-27,y-20),(x+27,y+20))
            else:
                b=math.tan(math.pi/8)
                space.add_lwpolyline([(x-24,y-60,0),(x+24,y-60,b),(x+48,y-36,0),(x+48,y+36,b),
                    (x+24,y+60,0),(x-24,y+60,b),(x-48,y+36,0),(x-48,y-36,b)],format="xyb",close=True)
            space.add_text(n.id,dxfattribs={"height":14,"style":"GB"}).set_placement((x,y+5),align=TextEntityAlignment.MIDDLE_CENTER)
            space.add_text(n.label,dxfattribs={"height":12,"style":"GB"}).set_placement((x,y-18),align=TextEntityAlignment.MIDDLE_CENTER)
        for e,points in zip(p.edges,segments):
            space.add_lwpolyline([(x,-y) for x,y in points])
            x,y=points[-1]; px,py=points[-2]
            theta=math.atan2(y-py,x-px)
            for offset in (-.45,.45):
                space.add_line((x,-y),(x-10*math.cos(theta+offset),-y+10*math.sin(theta+offset)))
            space.add_text(e.label,dxfattribs={"height":10,"style":"GB","insert":(points[0][0],-points[0][1]+10)})
        record=doc.rootdict.add_xrecord("CHEMELLM_TOPOLOGY")
        # R2004 is a legacy text encoding; ASCII JSON avoids DXF \U+ escapes inside JSON strings.
        raw=json.dumps(p.model_dump(exclude_none=True),ensure_ascii=True,separators=(",",":"))
        record.reset([(1,raw[i:i+200]) for i in range(0,len(raw),200)])
        path=folder/(safe_name(p.title)+".dxf")
        doc.saveas(path)
        from app.drawing_io import dxf_summary
        cad_preview=folder/"cad-preview.svg"
        summary=dxf_summary(path,cad_preview)
        pending.append({"path":path,"preview":cad_preview if cad_preview.exists() else None,
            "metadata":{**metadata,"cad_version":"AC1018 / AutoCAD 2004","entity_counts":summary["entity_counts"],"font_note":"gbcbig.shx 仅引用；字体需自行合法安装。DXF 是拓扑交换草图，尚非合规设计图集。"}})
    except ImportError:
        metadata["warnings"].append("ezdxf 未安装，未生成 CAD 文件。")
    records=[artifact_record(project_id,**record) for record in pending]
    return {"artifacts":records,"status":"draft","warnings":metadata["warnings"]}
