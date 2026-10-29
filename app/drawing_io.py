"""Read drawing content without pretending geometric symbols prove process semantics."""
import html
import json
import math
import re
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from pydantic import Field
from app.schemas import Inputs
from app.config import ARTIFACTS
from app.db import one

SVG_TAGS={"svg","g","defs","marker","path","rect","circle","ellipse","line","polyline","polygon",
          "text","tspan","title","desc","metadata","linearGradient","radialGradient","stop","clipPath"}
SVG_ATTRS={"id","viewBox","width","height","x","y","x1","x2","y1","y2","cx","cy","r","rx","ry",
           "d","points","fill","fill-rule","fill-opacity","stroke","stroke-width","stroke-opacity","stroke-linecap",
           "stroke-linejoin","stroke-dasharray","opacity","transform","font-family","font-size","font-weight",
           "font-style","text-anchor","dominant-baseline","dx","dy","marker-start","marker-mid","marker-end",
           "markerWidth","markerHeight","refX","refY","orient","markerUnits","preserveAspectRatio","version",
           "offset","stop-color","stop-opacity","gradientUnits","gradientTransform","clip-path","role","aria-label"}
CSS_PROPERTIES={"fill","stroke","stroke-width","font-size","font-family","font-weight","opacity","text-anchor",
                "stroke-linejoin","stroke-linecap","fill-opacity","stroke-opacity","stroke-dasharray"}
TAG_PATTERN=re.compile(r"\b(?:TK|HX|V|R|T|E|P|F|K|C|FI|FT|FC|TI|TT|TC|PI|PT|PC|LI|LT|LC)-[A-Za-z0-9]{1,12}\b")

class DrawingReadInput(Inputs):
    artifact_id:str=Field(min_length=1,max_length=40)

def safe_svg(content):
    if b"<!doctype" in content.lower() or b"<!entity" in content.lower():
        raise ValueError("SVG 不接受 DTD 或实体声明")
    try:
        root=ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("SVG XML 无法解析") from exc
    if root.tag.split("}")[-1]!="svg":
        raise ValueError("文件根元素不是 SVG")
    count=0
    for node in root.iter():
        count+=1
        if count>20000:
            raise ValueError("SVG 元素数量超过20000")
        namespace=node.tag[1:].split("}")[0] if node.tag.startswith("{") else ""
        if namespace not in {"","http://www.w3.org/2000/svg"} or node.tag.split("}")[-1] not in SVG_TAGS:
            raise ValueError("SVG 含不支持的元素："+node.tag.split("}")[-1])
        for attr,value in list(node.attrib.items()):
            if attr=="style":
                for declaration in value.split(";"):
                    if not declaration.strip():continue
                    prop,sep,val=declaration.partition(":")
                    if not sep or prop.strip() not in CSS_PROPERTIES:
                        raise ValueError("SVG 内联样式含不支持的属性")
                    if re.search(r"url\s*\(|expression|javascript|@|\\",val,re.I):
                        raise ValueError("SVG 样式不接受外部资源或动态表达式")
                continue
            if attr not in SVG_ATTRS:
                raise ValueError("SVG 含不支持的属性："+attr)
            if re.search(r"url\s*\(",value,re.I) and not re.fullmatch(r"url\(#[A-Za-z0-9_.:-]+\)",value):
                raise ValueError("SVG 只允许引用本文件内的图形资源")
            if re.search(r"javascript:|data:|https?:|file:",value,re.I):
                raise ValueError("SVG 不接受外部资源地址")
    return root

def topology_from_json(value):
    from app.diagrams import DiagramInput
    return DiagramInput.model_validate(value).model_dump(exclude_none=True)

def svg_summary(content):
    root=safe_svg(content)
    texts=[]
    topology=None
    for node in root.iter():
        tag=node.tag.split("}")[-1]
        if tag in {"text","title","desc"}:
            value="".join(node.itertext()).strip()
            if value:texts.append({"text":value[:1000],"x":node.get("x"),"y":node.get("y")})
        if tag=="metadata" and node.get("id")=="chemellm-topology":
            try:
                topology=topology_from_json(json.loads(node.text or ""))
            except (ValueError,TypeError):
                raise ValueError("SVG 内置流程拓扑格式错误")
    return {"format":"svg","viewBox":root.get("viewBox"),"text_elements":texts[:1500],
            "entity_counts":dict(Counter(n.tag.split("}")[-1] for n in root.iter())),"encoded_topology":topology}

def dxf_summary(path,preview_path=None):
    import ezdxf
    try:
        doc=ezdxf.readfile(path)
    except (ezdxf.DXFError,UnicodeError,OSError) as exc:
        raise ValueError("DXF 无法读取；请提供ASCII DXF（2004版或兼容版本）") from exc
    model=doc.modelspace()
    if len(model)>20000:
        raise ValueError("DXF 模型空间实体超过20000")
    topology=None
    encoded=doc.rootdict.get("CHEMELLM_TOPOLOGY")
    if encoded is not None:
        try:
            raw="".join(tag.value for tag in encoded.tags if tag.code==1)
            topology=topology_from_json(json.loads(raw))
        except (AttributeError,ValueError,TypeError):
            raise ValueError("DXF 内置拓扑数据无效")
    texts=[];shapes=[];bounds=[];counts=Counter();unsupported=Counter()
    def point(p):
        x,y=float(p[0]),-float(p[1])
        if not math.isfinite(x+y) or max(abs(x),abs(y))>1e12:
            raise ValueError("DXF 含不可预览的坐标")
        bounds.append((x,y))
        return x,y
    def visit(entity,depth=0):
        counts[entity.dxftype()]+=1
        if sum(counts.values())>30000 or depth>6:
            raise ValueError("DXF 展开后的实体或嵌套层级超过限制")
        kind=entity.dxftype()
        if kind=="INSERT":
            for child in entity.virtual_entities():visit(child,depth+1)
            for attrib in entity.attribs:visit(attrib,depth+1)
        elif kind=="LINE":
            a,b=point(entity.dxf.start),point(entity.dxf.end)
            shapes.append(f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}"/>')
        elif kind in {"LWPOLYLINE","POLYLINE"}:
            from ezdxf.path import make_path
            points=[point(p) for p in make_path(entity).flattening(distance=0.25,segments=4)]
            if len(points)>10000:raise ValueError("DXF 曲线离散点超过限制")
            shapes.append('<polyline points="'+" ".join(f"{x},{y}" for x,y in points)+'" fill="none"/>')
        elif kind in {"CIRCLE","ARC"}:
            x,y=point(entity.dxf.center)
            r=abs(float(entity.dxf.radius))
            if not math.isfinite(r) or r>1e12:raise ValueError("DXF 圆弧半径无效")
            bounds.extend([(x-r,y-r),(x+r,y+r)])
            if kind=="CIRCLE":shapes.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="none"/>')
            else:
                start,end=float(entity.dxf.start_angle),float(entity.dxf.end_angle)
                span=(end-start)%360
                angle=[math.radians(start+span*i/64) for i in range(65)]
                shapes.append('<polyline points="'+" ".join(f"{x+r*math.cos(a)},{y-r*math.sin(a)}" for a in angle)+'" fill="none"/>')
        elif kind in {"TEXT","MTEXT","ATTRIB"}:
            from ezdxf.lldxf.encoding import decode_dxf_unicode
            value=entity.plain_text() if hasattr(entity,"plain_text") else entity.dxf.text
            value=decode_dxf_unicode(value)
            x,y=point(entity.dxf.insert)
            halign=entity.dxf.get("halign",0) if kind!="MTEXT" else 0
            valign=entity.dxf.get("valign",0) if kind!="MTEXT" else 0
            if halign or valign:
                x,y=point(entity.dxf.get("align_point",entity.dxf.insert))
            anchor={1:"middle",2:"end"}.get(halign,"start")
            rotation=-float(entity.dxf.get("rotation",0))
            if not math.isfinite(rotation):raise ValueError("DXF 文字旋转角度无效")
            height=float(entity.dxf.get("char_height",2.5) if kind=="MTEXT" else entity.dxf.get("height",2.5))
            texts.append({"text":value[:1000],"x":x,"y":-y})
            if height<=0 or not math.isfinite(height):height=2.5
            bounds.extend([(x+min(len(value),200)*height*.8,y-height)])
            for i,line in enumerate(value.splitlines()[:100]):
                baseline=' dominant-baseline="middle"' if valign==2 else ''
                shapes.append(f'<text x="{x}" y="{y+i*height*1.5}" text-anchor="{anchor}"{baseline} transform="rotate({rotation} {x} {y})" font-size="{height}" stroke="none" fill="#203b35">{html.escape(line[:1000])}</text>')
        else:unsupported[kind]+=1
    for entity in model:visit(entity)
    if preview_path and bounds:
        xs,ys=zip(*bounds)
        width,height=max(max(xs)-min(xs),1),max(max(ys)-min(ys),1)
        margin=max(width,height)*.04
        stroke=max(width,height)/1500
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min(xs)-margin} {min(ys)-margin} {width+2*margin} {height+2*margin}"><rect x="{min(xs)-margin}" y="{min(ys)-margin}" width="{width+2*margin}" height="{height+2*margin}" fill="#fbfcf8"/><g stroke="#256756" stroke-width="{stroke}" fill="none" font-family="sans-serif">'
        preview_path.write_text(svg+"".join(shapes)+"</g></svg>",encoding="utf-8")
    return {"format":"dxf","cad_version":doc.dxfversion,"entity_counts":dict(counts),"unsupported_entity_counts":dict(unsupported),
            "text_elements":texts[:1500],"encoded_topology":topology,"drawing_units_code":doc.header.get("$INSUNITS",0)}

def read_drawing(project_id,p):
    row=one("SELECT * FROM artifacts WHERE id=? AND project_id=?",(p.artifact_id,project_id))
    if not row:
        raise ValueError("图纸文件不属于当前项目或不存在")
    path=Path(row["path"]).resolve()
    if not path.is_relative_to(ARTIFACTS):
        raise ValueError("图纸路径无效")
    if row["kind"]=="svg":summary=svg_summary(path.read_bytes())
    elif row["kind"]=="dxf":summary=dxf_summary(path)
    elif row["kind"]=="json":
        summary={"format":"json","encoded_topology":topology_from_json(json.loads(path.read_text(encoding="utf-8"))),"text_elements":[]}
    elif row["kind"]=="pdf":
        metadata=json.loads(row["metadata"])
        texts=[{"page":page["page"],"text":page["text"][:10000]} for page in metadata.get("pages",[])[:30]]
        if not texts and row["text"]:texts=[{"page":None,"text":row["text"][:10000]}]
        summary={"format":"pdf","text_elements":texts,"encoded_topology":None}
    else:
        raise ValueError("当前矢量识读支持SVG、DXF、流程JSON及文本PDF；图片/扫描图需要另行接入视觉模型")
    candidates=sorted({tag for item in summary["text_elements"] for tag in TAG_PATTERN.findall(item["text"])})
    return {"artifact_id":row["id"],"name":row["name"],"source_sha256":json.loads(row["metadata"]).get("sha256"),
            **summary,"equipment_tag_candidates":candidates,
            "topology_status":"encoded_unverified" if summary["encoded_topology"] else "not_reconstructed",
            "warnings":["设备位号候选由可读取文字匹配得到，不能证明符号类型、管线连接或控制关系。",
                        "内置拓扑若存在，是文件声明的结构化数据；尚未核对其与图形逐一一致。没有内置拓扑的图纸不会猜造连接。",
                        "DXF预览为XY投影，曲线离散化；未支持实体会列出。字体、标注和布局的完整保真需要CAD软件复核。"]}
