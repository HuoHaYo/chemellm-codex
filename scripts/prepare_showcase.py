"""Export a fixed, reviewed selection of public project visuals (never whole projects)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from PIL import Image
from pypdf import PdfReader, PdfWriter
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'site/showcase'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fr2023', type=Path, required=True)
    parser.add_argument('--tianzheng', type=Path, required=True)
    parser.add_argument('--three-vendor', type=Path, required=True)
    args = parser.parse_args()
    entries = []

    def save(name, content, source, original=None):
        target = PUBLIC/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        entry = dict(path='showcase/'+name, bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), source=source)
        if original:
            entry['source_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
        entries.append(entry)

    def picture(source, name, width=1800):
        import io
        with Image.open(source) as image:
            image = image.convert('RGB')
            image.thumbnail((width, width))
            output = io.BytesIO()
            image.save(output, 'WEBP', quality=88, method=6)
        save(name, output.getvalue(), source.name, source)

    def document(source, name, title):
        reader = PdfReader(source)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
            for key in ('/Annots', '/AA'):
                writer.pages[-1].pop(key, None)
        writer.add_metadata({'/Title': title, '/Author': 'ChemELLM', '/Subject': '公开案例展示；研究与初步设计成果，按页面说明使用。'})
        import io
        output = io.BytesIO()
        writer.write(output)
        save(name, output.getvalue(), source.name, source)

    fr = args.fr2023/'delivery/giant-total-pfd'
    for filename, name in [('总PFD_高清总览.png','overview'),('crude-1.png','crude'),('ethylene-1.png','ethylene'),('arom-1.png','aromatics')]:
        picture(fr/'预览'/filename, 'fr2023/'+name+'.webp')
    svg = (fr/'福建联合石化_单张巨幅总PFD.svg').read_text(encoding='utf-8')
    # The original embeds a machine font subset; public SVG uses the reader's fonts.
    svg = re.sub(r'<style\b[^>]*>.*?</style>', '', svg, flags=re.S)
    svg = svg.replace('<svg ', '<svg font-family="system-ui, Microsoft YaHei, sans-serif" ', 1)
    save('fr2023/pfd.svg', svg.encode(), '巨幅总PFD矢量图', fr/'福建联合石化_单张巨幅总PFD.svg')
    source_html=(fr/'福建联合石化_单张巨幅总PFD_缩放浏览.html').read_text(encoding='utf-8')
    zones=json.loads(re.search(r'const zones=(\{[^\n]+\});',source_html).group(1))
    save('fr2023/zones.json',json.dumps(zones,ensure_ascii=False).encode(),'巨幅总PFD分区坐标')
    document(fr/'福建联合石化_单张巨幅总PFD.pdf', 'fr2023/pfd.pdf', 'FR2023 单张巨幅研究总PFD')
    document(args.fr2023/'reports/工程研究报告.pdf', 'fr2023/research.pdf', 'FR2023 阶段工程研究报告')

    tz = args.tianzheng/'deliverables'
    picture(tz/'06_三维设计/preview.png','tianzheng/overview.webp')
    documents=[('01_可行性研究/项目可行性研究报告.pdf','feasibility','项目可行性研究报告'),
        ('02_初步设计/初步设计说明书.pdf','design','初步设计说明书'),
        ('03_设备计算/典型设备工艺设计计算说明书.pdf','equipment','典型设备工艺设计计算说明书'),
        ('04_设计图集/设计图集.pdf','drawings','设计图集'),
        ('07_安全与节能专题/安全环保与节能经济专题.pdf','safety','安全环保与节能经济专题')]
    for relative,name,title in documents:
        document(tz/relative,'tianzheng/'+name+'.pdf','2026天正杯 '+title)
    pdf=pdfium.PdfDocument(tz/'04_设计图集/设计图集.pdf')
    for index,name in [(0,'pfd'),(2,'pid'),(8,'layout')]:
        page=pdf[index]
        bitmap=page.render(scale=1800/page.get_width())
        image=bitmap.to_pil().convert('RGB')
        import io
        output=io.BytesIO()
        image.save(output,'WEBP',quality=92,method=6)
        save('tianzheng/'+name+'.webp',output.getvalue(),'设计图集第'+str(index+1)+'页',tz/'04_设计图集/设计图集.pdf')
        image.close();bitmap.close();page.close()
    pdf.close()
    original=tz/'06_三维设计/scene.glb'
    binary=original.read_bytes()
    size,kind=struct.unpack_from('<II',binary,12)
    assert kind==0x4E4F534A
    model=json.loads(binary[20:20+size])
    def clean(value):
        if isinstance(value,dict):
            value.pop('extras',None)
            for child in value.values():clean(child)
        elif isinstance(value,list):
            for child in value:clean(child)
    clean(model)
    for collection in ('buffers','images'):
        assert not any(item.get('uri') for item in model.get(collection,[])), 'Model must be self-contained'
    content=json.dumps(model,ensure_ascii=False,separators=(',',':')).encode()
    content+=b' '*((-len(content))%4)
    tail=binary[20+size:]
    result=struct.pack('<III',0x46546C67,2,20+len(content)+len(tail))+struct.pack('<II',len(content),kind)+content+tail
    save('tianzheng/scene.glb',result,'Blender厂区GLB；公开版移除附加元数据',original)

    for name in ('three.module.js','GLTFLoader.js','OrbitControls.js','BufferGeometryUtils.js','LICENSE'):
        source=args.three_vendor/name
        text=source.read_text(encoding='utf-8').replace("from 'three'", "from './three.module.js'")
        text=text.replace('\t\t\t \tmaterial =', '\t\t\t\tmaterial =')
        save('vendor/'+name,text.encode(),'Three.js r170 / MIT',source)
    (PUBLIC/'assets.json').write_text(json.dumps({'schema_version':1,'assets':entries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Prepared {len(entries)} selected assets, {sum(e["bytes"] for e in entries):,} bytes')


if __name__=='__main__':
    main()
