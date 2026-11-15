"""Native QET 0.100 wiring generation; previews are rendered by ChemELLM.

No GUI automation or claimed electrical/functional-safety certification.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET
from app.qelectrotech import QElectroTechInput

WIDTH, HEIGHT = 1600, 1000
BOUNDARY = '自动检查仅覆盖连接完整性、端子与网名一致性；未验证电气选型、防爆、本安、SIS 或施工合规。'
STYLE = 'line-style:normal;line-weight:normal;filling:none;color:black'


def _uuid():
    return '{'+str(uuid.uuid4())+'}'


def geometry(component, index):
    x = component.x if component.x is not None else 140+380*(index % 4)
    y = component.y if component.y is not None else 160+260*(index // 4)
    sides = {s: [p for p in component.terminals if p.side == s] for s in ('left', 'right', 'top', 'bottom')}
    width = max(140, 30*(max(len(sides['top']), len(sides['bottom']))+1))
    height = max(80, 24*(max(len(sides['left']), len(sides['right']))+1))
    pins = {}
    for side, terminals in sides.items():
        for i, pin in enumerate(terminals):
            if side in {'left', 'right'}:
                px, py = (-width/2-10 if side == 'left' else width/2+10), (i+1)*height/(len(terminals)+1)-height/2
            else:
                px, py = (i+1)*width/(len(terminals)+1)-width/2, (-height/2-10 if side == 'top' else height/2+10)
            pins[pin.id] = (px, py)
    return x, y, width, height, pins


def validate(request: QElectroTechInput):
    blockers, warnings = [], []
    counts = {'folios': len(request.folios), 'components': 0, 'terminals': 0, 'wires': 0}
    global_ids, global_wire_ids, cable_cores = set(), set(), set()
    for page in request.folios:
        components = {c.id: c for c in page.components}
        pins = {(c.id, p.id): p for c in page.components for p in c.terminals}
        parent = {p: p for p in pins}
        used, pairs, boxes = set(), set(), []
        def find(p):
            while parent[p] != p:
                parent[p] = parent[parent[p]]
                p = parent[p]
            return p
        for i, c in enumerate(page.components):
            if c.id in global_ids:
                blockers.append('元件位号重复：'+c.id)
            global_ids.add(c.id)
            x, y, width, height, _ = geometry(c, i)
            box = (x-width/2-15, y-height/2-35, x+width/2+15, y+height/2+35)
            if box[0] < 25 or box[1] < 70 or box[2] > WIDTH-25 or box[3] > HEIGHT-100:
                blockers.append('元件超出图页边界，请拆页或调整绘图坐标：'+c.id)
            if any(box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1] for b in boxes):
                blockers.append('元件绘图范围重叠：'+c.id)
            boxes.append(box)
        for wire in page.wires:
            if wire.id in global_wire_ids:
                blockers.append('导线编号重复：'+wire.id)
            global_wire_ids.add(wire.id)
            a, b = (wire.start.component, wire.start.terminal), (wire.end.component, wire.end.terminal)
            if a not in pins or b not in pins:
                blockers.append('导线引用不存在的元件或端子：'+wire.id)
                continue
            if a == b:
                blockers.append('导线两端是同一端子：'+wire.id)
            pair = frozenset((a, b))
            if pair in pairs:
                blockers.append('相同端子对重复连线：'+wire.id)
            pairs.add(pair)
            used.update((a, b)); parent[find(a)] = find(b)
            if bool(wire.cable) != bool(wire.core):
                blockers.append('电缆编号和芯线编号必须同时明确：'+wire.id)
            if wire.cable and wire.core:
                key = wire.cable, wire.core
                if key in cable_cores:
                    blockers.append('电缆芯线被重复占用：'+wire.cable+'/'+wire.core)
                cable_cores.add(key)
            if wire.section_mm2 is None:
                warnings.append('导线截面积待选型：'+wire.id)
        nets = {}
        for pin in used:
            if pins[pin].potential:
                nets.setdefault(find(pin), set()).add(pins[pin].potential)
        for potentials in nets.values():
            if len(potentials) > 1:
                blockers.append('同一导电网络出现冲突网名：'+', '.join(sorted(potentials)))
        for c in page.components:
            if not any(key[0] == c.id for key in used):
                blockers.append('元件未连接任何导线：'+c.id)
        unused = len(pins.keys()-used)
        if unused:
            warnings.append(f'{page.title}：{unused} 个端子未使用，需确认是否为备用')
        counts['components'] += len(page.components)
        counts['terminals'] += len(pins)
        counts['wires'] += len(page.wires)
    return {'passed': not blockers, 'blockers': blockers, 'warnings': warnings,
            'counts': counts, 'scope': BOUNDARY, 'native_reload_verified': False}


def _text(parent, tag, content, **attrs):
    element = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    element.text = content
    return element


def native_project(request):
    project = ET.Element('project', title=request.title, version='0.100', uuid=_uuid())
    collection = ET.SubElement(project, 'collection')
    category = ET.SubElement(collection, 'category', name='chemellm')
    names = ET.SubElement(category, 'names'); _text(names, 'name', 'ChemELLM wiring', lang='en')
    for page_no, folio in enumerate(request.folios, 1):
        diagram = ET.SubElement(project, 'diagram', title=folio.title, version='0.100',
            order=str(page_no), uuid=_uuid(), cols='20', colsize='80', rows='12', rowsize='80',
            height='1020', displaycols='true', displayrows='true', displayAt='bottom',
            author='ChemELLM', date='null', folio=f'{page_no}/{len(request.folios)}')
        elements = ET.SubElement(diagram, 'elements')
        conductors = ET.SubElement(diagram, 'conductors')
        terminals, pin_counter = {}, 0
        for i, component in enumerate(folio.components):
            x, y, width, height, pins = geometry(component, i)
            filename = f'folio{page_no}_component{i}.elmt'
            definition = ET.SubElement(ET.SubElement(category, 'element', name=filename), 'definition',
                type='element', version='0.100', width=str(int(width+40)), height=str(int(height+60)),
                hotspot_x=str(int(width/2+20)), hotspot_y=str(int(height/2+30)))
            names = ET.SubElement(definition, 'names'); _text(names, 'name', component.kind, lang='en')
            description = ET.SubElement(definition, 'description')
            ET.SubElement(description, 'rect', x=str(-width/2), y=str(-height/2),
                          width=str(width), height=str(height), style=STYLE, antialias='false')
            _text(description, 'text', None, x=str(-width/2+8), y=str(height/2+18), size='8', text=component.id)
            element = ET.SubElement(elements, 'element', type='embed://chemellm/'+filename,
                uuid=_uuid(), orientation='0', x=str(x), y=str(y), z='10', prefix='', freezeLabel='true')
            native_terminals = ET.SubElement(element, 'terminals')
            info = ET.SubElement(element, 'elementInformations')
            _text(info, 'elementInformation', component.id, name='label', show='1')
            if component.label:
                ET.SubElement(description, 'text', x=str(-width/2+8), y=str(height/2+34), size='7', text=component.label)
            ET.SubElement(element, 'inputs')
            for pin in component.terminals:
                px, py = pins[pin.id]
                ET.SubElement(description, 'terminal', x=str(px), y=str(py),
                              orientation={'left': 'w', 'right': 'e', 'top': 'n', 'bottom': 's'}[pin.side])
                ET.SubElement(description, 'text', x=str(px+12 if pin.side == 'left' else px-24),
                              y=str(py-4), size='6', text=pin.id)
                # QET serializes the element-side dock, four drawing units inward
                # from the conductor-side point stored in the symbol definition.
                dx, dy = {'left': (4, 0), 'right': (-4, 0), 'top': (0, 4), 'bottom': (0, -4)}[pin.side]
                ET.SubElement(native_terminals, 'terminal', id=str(pin_counter), name=pin.id, number=pin.id,
                    x=str(px+dx), y=str(py+dy), orientation=str({'top': 0, 'right': 1, 'bottom': 2, 'left': 3}[pin.side]))
                terminals[component.id, pin.id] = pin_counter
                pin_counter += 1
        for wire in folio.wires:
            ET.SubElement(conductors, 'conductor', terminal1=str(terminals[wire.start.component, wire.start.terminal]),
                terminal2=str(terminals[wire.end.component, wire.end.terminal]), type='multi', num=wire.id,
                cable=wire.cable, conductor_section=str(wire.section_mm2 or ''), function=wire.core,
                condsize='1', color='#000000', text_color='#000000', displaytext='1', numsize='7',
                horizrotatetext='0', vertirotatetext='270', freezeLabel='true')
    # QET accepts collection anywhere, but write it last as its native serializer does.
    project.remove(collection); project.append(collection)
    ET.indent(project)
    return ET.tostring(project, encoding='utf-8', xml_declaration=True)


def primitives(request, folio, index):
    shapes = [('text', 35, 35, request.title+' / '+folio.title, 18),
              ('text', 35, HEIGHT-65, 'ChemELLM 连接预览；原生 QET 自动布线外观可能不同。', 12),
              ('text', 35, HEIGHT-40, BOUNDARY, 12),
              ('text', WIDTH-100, HEIGHT-15, str(index+1), 12)]
    pins = {}
    for i, component in enumerate(folio.components):
        x, y, width, height, coords = geometry(component, i)
        shapes.extend([('rect', x-width/2, y-height/2, width, height),
                       ('text', x-width/2+8, y+height/2+18, component.id, 14)])
        if component.label:
            shapes.append(('text', x-width/2+8, y+height/2+35, component.label, 12))
        for pin in component.terminals:
            px, py = coords[pin.id]; pins[component.id, pin.id] = x+px, y+py
            ex, ey = (x-width/2 if pin.side == 'left' else x+width/2 if pin.side == 'right' else x+px), (y-height/2 if pin.side == 'top' else y+height/2 if pin.side == 'bottom' else y+py)
            shapes.extend([('line', x+px, y+py, ex, ey),
                           ('text', x+px+4, y+py-5, pin.id, 9)])
    for i, wire in enumerate(folio.wires):
        a, b = pins[wire.start.component, wire.start.terminal], pins[wire.end.component, wire.end.terminal]
        middle = (a[0]+b[0])/2 + (i % 5)*8
        points = [a, (middle, a[1]), (middle, b[1]), b]
        shapes.extend(('line', *left, *right) for left, right in zip(points, points[1:]))
        shapes.append(('text', middle+4, (a[1]+b[1])/2-7, wire.id, 9))
    return shapes


def _svg(shapes):
    elements = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}">',
                '<rect width="100%" height="100%" fill="white"/>', '<g stroke="#24364b" fill="none" stroke-width="1.3">']
    for kind, a, b, c, d in shapes:
        if kind == 'rect': elements.append(f'<rect x="{a}" y="{b}" width="{c}" height="{d}"/>')
        elif kind == 'line': elements.append(f'<line x1="{a}" y1="{b}" x2="{c}" y2="{d}"/>')
        else: elements.append(f'<text x="{a}" y="{b}" fill="#162739" stroke="none" font-family="Microsoft YaHei,sans-serif" font-size="{d}">{html.escape(c)}</text>')
    return '\n'.join(elements+['</g></svg>'])


def build(request, folder: Path):
    validation = validate(request)
    if not validation['passed']:
        raise ValueError('; '.join(validation['blockers'])[:2000])
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    folder.mkdir(parents=True, exist_ok=True)
    native = folder/'接线工程.qet'; native.write_bytes(native_project(request))
    inputs = folder/'qet-input.json'; inputs.write_text(request.model_dump_json(indent=2), encoding='utf-8')
    report = folder/'qet-check.json'; report.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')
    pdf = folder/'接线图.pdf'; document = canvas.Canvas(str(pdf), pagesize=(WIDTH*.7, HEIGHT*.7))
    document.setTitle(request.title)
    files = [native, pdf, inputs, report]
    rows = []
    for index, folio in enumerate(request.folios):
        shapes = primitives(request, folio, index)
        svg = folder/f'接线图-{index+1:02d}.svg'; svg.write_text(_svg(shapes), encoding='utf-8'); files.append(svg)
        for kind, a, b, c, d in shapes:
            if kind == 'text':
                document.setFont('STSong-Light', d*.7); document.drawString(a*.7, (HEIGHT-b)*.7, c)
            elif kind == 'rect': document.rect(a*.7, (HEIGHT-b-d)*.7, c*.7, d*.7)
            else: document.line(a*.7, (HEIGHT-b)*.7, c*.7, (HEIGHT-d)*.7)
        document.showPage()
        by_id = {c.id: c for c in folio.components}
        for wire in folio.wires:
            rows.append([folio.title, wire.id, wire.start.component, wire.start.terminal,
                         wire.end.component, wire.end.terminal, wire.cable, wire.core,
                         wire.section_mm2 if wire.section_mm2 is not None else '待选型', 'mm2',
                         wire.source or request.source,
                         by_id[wire.start.component].source or request.source,
                         by_id[wire.end.component].source or request.source])
    document.save()
    table = folder/'连接表.csv'
    with table.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(['图页', '导线', '起点元件', '起点端子', '终点元件', '终点端子',
            '电缆', '芯线', '截面积', '单位', '连接来源', '起点来源', '终点来源']); writer.writerows(rows)
    files.append(table)
    check = inspect(native)
    if check['counts'] != validation['counts'] or check['missing_connections']:
        raise ValueError('原生工程序列化连接核对失败')
    return {'files': files, 'validation': validation, 'native_file': native,
            'native_schema_verified': True, 'preview_engine': 'ChemELLM vectors / ReportLab',
            'warnings': validation['warnings']+[BOUNDARY, 'QET 0.100 无批量导出接口；PDF/SVG 由共同连接数据绘制，未声称原生重载验证。']}


def inspect(path: Path):
    if path.suffix.lower() != '.qet' or path.stat().st_size > 16*1024*1024:
        raise ValueError('inspect 仅接受不超过 16 MiB 的 XML .qet 工程')
    raw = path.read_bytes()
    try:
        root = SafeET.fromstring(raw)
    except Exception as exc:
        raise ValueError('无效或不安全的 QET XML：'+str(exc)[:300]) from exc
    if root.tag != 'project':
        raise ValueError('不是 QElectroTech project')
    counts = {'folios': 0, 'components': 0, 'terminals': 0, 'wires': 0}
    missing, summary = [], []
    for index, diagram in enumerate(root.findall('diagram'), 1):
        elements = diagram.findall('elements/element'); wires = diagram.findall('conductors/conductor')
        pins = {}
        for element in elements:
            definition = None
            location = element.get('type', '')
            if location.startswith('embed://'):
                parts = location.removeprefix('embed://').split('/')
                node = root.find('collection')
                for part in parts[:-1]:
                    node = next((n for n in node.findall('category') if n.get('name') == part), None) if node is not None else None
                embedded = next((n for n in node.findall('element') if n.get('name') == parts[-1]), None) if node is not None else None
                definition = embedded.find('definition') if embedded is not None else None
                if definition is None:
                    missing.append(f'folio {index}: missing embedded symbol '+location)
            expected_pins = set()
            if definition is not None:
                for pin in definition.findall('description/terminal'):
                    try:
                        orientation, dx, dy = {'n': ('0', 0, 4), 'e': ('1', -4, 0),
                            's': ('2', 0, -4), 'w': ('3', 4, 0)}[pin.get('orientation')]
                        expected_pins.add((round(float(pin.get('x'))+dx, 6), round(float(pin.get('y'))+dy, 6), orientation))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError('内嵌符号端子方向或坐标无效') from exc
            for terminal in element.findall('terminals/terminal'):
                key = terminal.get('id')
                if key in pins: raise ValueError('图页内端子 ID 重复')
                pins[key] = terminal
                if definition is not None:
                    try:
                        actual = (round(float(terminal.get('x')), 6), round(float(terminal.get('y')), 6), terminal.get('orientation'))
                    except (ValueError, TypeError) as exc:
                        raise ValueError('实例端子坐标无效') from exc
                    if actual not in expected_pins:
                        missing.append(f'folio {index}: terminal does not bind to symbol: '+str(key))
        for wire in wires:
            if wire.get('terminal1') not in pins or wire.get('terminal2') not in pins:
                missing.append(f'folio {index}: '+str(wire.get('num', '')))
        counts['folios'] += 1; counts['components'] += len(elements)
        counts['terminals'] += len(pins); counts['wires'] += len(wires)
        if len(summary) < 20:
            summary.append({'title': diagram.get('title'), 'components': len(elements), 'wires': len(wires)})
    return {'title': root.get('title'), 'version': root.get('version'), 'counts': counts,
            'folios': summary, 'missing_connections': missing[:50],
            'sha256': hashlib.sha256(raw).hexdigest(), 'native_reload_verified': False,
            'note': 'XML 结构与引用检查，不执行 QET GUI，不推断控制逻辑。'}
