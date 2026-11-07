"""Isolated DWSIM Automation3 process; DWSIM stays an external installation."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import uuid
import zipfile
import copy
import xml.etree.ElementTree as ET
from defusedxml import ElementTree as SafeET

from app.dwsim import DwsimInput
from app.dwsim_specs import compile_spec
from app.external_tools import find_dwsim_home

HERE = Path(__file__).resolve().parent
RUNNER = HERE / 'dwsim' / 'DwsimRunner.exe'
MAX_FILE = 25 * 1024 * 1024


def find_home() -> Path | None:
    return find_dwsim_home()


def runner_needs_build() -> bool:
    """Check content rather than checkout/extraction timestamps."""
    try:
        manifest = json.loads(RUNNER.with_suffix('.build.json').read_text(encoding='utf-8-sig'))
        sources = {source.name: hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                   for source in RUNNER.parent.glob('*.cs')}
        return (manifest.get('schema_version') != 1 or not sources
                or manifest.get('sources') != sources
                or manifest.get('runner_sha256') != hashlib.sha256(RUNNER.read_bytes()).hexdigest())
    except (OSError, ValueError):
        return True


def status() -> dict:
    home = find_home()
    runner = RUNNER.is_file()
    needs_build = runner_needs_build()
    samples = sorted([*home.glob('samples/*.dwxmz'), *home.glob('samples/*.dwxml')]) if home else []
    return {'installed': bool(home), 'home': str(home) if home else '',
            'automation_dll': bool(home), 'runner': runner, 'needs_build': needs_build,
            'ready': bool(home and runner and not needs_build and os.name == 'nt'),
            'actions': ['status', 'catalog', 'inspect', 'check', 'create', 'run', 'layout'],
            'samples': [{'name': path.name, 'path': str(path)} for path in samples],
            'scope': '稳态热工、分离、循环及转化率/平衡反应；不支持动态与动力学速率表达式',
            'note': '调用本机 DWSIM Automation3，不随插件分发 DWSIM；未运行时不宣称模型已求解'}


def validate_flowsheet(path: Path) -> None:
    if not path.is_file() or path.suffix.lower() not in {'.dwxmz', '.dwxml'}:
        raise ValueError('flowsheet_path 必须是存在的 .dwxmz 或 .dwxml 文件')
    if path.stat().st_size > MAX_FILE:
        raise ValueError('DWSIM 文件不得超过25 MiB')
    if path.suffix.lower() == '.dwxmz':
        try:
            with zipfile.ZipFile(path) as archive:
                files = archive.infolist()
                if len(files) > 2000 or sum(item.file_size for item in files) > 100 * 1024 * 1024:
                    raise ValueError('DWSIM 解压尺寸超限')
                for item in files:
                    name = item.filename.replace('\\', '/')
                    parts = PurePosixPath(name)
                    if parts.is_absolute() or '..' in parts.parts or ':' in name:
                        raise ValueError('DWSIM 压缩包包含越界路径')
        except zipfile.BadZipFile as exc:
            raise ValueError('DWSIM 压缩包格式无效') from exc


def invoke(request: DwsimInput, work_dir: Path, *, save_path: Path | None = None,
           native_layout: dict | None = None) -> dict:
    home = find_home()
    if home is None:
        raise ValueError('未找到 DWSIM.Automation.dll；请安装 DWSIM 或设置 DWSIM_HOME')
    if not RUNNER.is_file():
        raise ValueError('DWSIM 运行器缺失；运行 scripts/build-dwsim-runner.ps1')
    if os.name != 'nt':
        raise ValueError('当前 DWSIM Automation3 运行器需要 Windows .NET Framework')
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = request.model_dump(mode='json')
    if native_layout is not None:
        payload['compiled_layout'] = native_layout
    groups = [payload.get('updates', [])] + payload.get('initialization', [])
    if payload.get('model'):
        groups.append(payload['model']['objects'])
    from app.dwsim import DwsimObject
    for group in groups:
        for spec in group:
            resolved = compile_spec(DwsimObject.model_validate(spec))
            spec['parameters'] = {**spec.get('parameters', {}), **resolved}
    if request.flowsheet_path:
        source = Path(request.flowsheet_path).resolve()
        validate_flowsheet(source)
        payload['flowsheet_path'] = str(source)
    if save_path:
        payload['save_path'] = str(save_path.resolve())
    request_path, output_path = work_dir/'runner-request.json', work_dir/'runner-result.json'
    request_path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    env = {**os.environ, 'TEMP': str(work_dir), 'TMP': str(work_dir)}
    command = [str(RUNNER), str(request_path), str(output_path), str(home)]
    try:
        with (work_dir/'runner.stdout.log').open('w', encoding='utf-8') as stdout, (work_dir/'runner.stderr.log').open('w', encoding='utf-8') as stderr:
            process = subprocess.run(command, stdout=stdout, stderr=stderr, env=env,
                                     timeout=request.timeout_seconds, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'DWSIM 超过 {request.timeout_seconds} 秒，已终止进程；输入和日志保存在 {work_dir}') from exc
    except OSError as exc:
        raise ValueError('DWSIM 运行器启动失败：'+str(exc)) from exc
    if not output_path.is_file():
        raise ValueError(f'DWSIM 未返回结果（退出码 {process.returncode}）；日志：{work_dir}')
    try:
        result = json.loads(output_path.read_text(encoding='utf-8-sig'),
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限数值：'+value)))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('DWSIM 返回无效 JSON；原始结果及日志：'+str(work_dir)) from exc
    if process.returncode != 0:
        raise ValueError('DWSIM 执行失败：'+str(result.get('error') or process.returncode)[:1800]+'；日志：'+str(work_dir))
    if result.get('engine') != 'DWSIM' or result.get('action') != request.action:
        raise ValueError('DWSIM 运行器协议不匹配；请运行 scripts/build-dwsim-runner.ps1')
    result['runner_sha256'] = hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    return result


LAYOUT_KEY = 'ChemELLM_layout_v1'
GRAPHIC_FIELDS = {'x':'X','y':'Y','width':'Width','height':'Height','rotation':'Rotation',
                  'flipped_h':'FlippedH','flipped_v':'FlippedV'}


def read_native(path: Path):
    """Read the one native simulation document, retaining other archive entries."""
    validate_flowsheet(path)
    entries = []
    if path.suffix.lower() == '.dwxml':
        root = SafeET.fromstring(path.read_bytes())
        xml_name = 'simulation.xml'
    else:
        roots = []
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                data = z.read(info)
                entries.append((info, data))
                if info.filename.lower().endswith(('.xml','.dwxml')):
                    try:
                        candidate = SafeET.fromstring(data)
                    except ET.ParseError:
                        continue
                    if candidate.tag == 'DWSIM_Simulation_Data':
                        roots.append((info.filename,candidate))
        if len(roots) != 1:
            raise ValueError('压缩模型须包含且仅包含一个 DWSIM 仿真 XML')
        xml_name, root = roots[0]
    if root.tag != 'DWSIM_Simulation_Data':
        raise ValueError('不是支持的 DWSIM 原生 XML')
    return root, xml_name, entries


def _compact(root):
    root = copy.deepcopy(root)
    for n in root.iter():
        if len(n) and n.text is not None and not n.text.strip():
            n.text = None
        if n.tail is not None and not n.tail.strip():
            n.tail = None
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def write_native(root, target: Path, xml_name='simulation.xml', entries=()):
    data = _compact(root)
    target.parent.mkdir(parents=True,exist_ok=True)
    pending = target.with_suffix(target.suffix+'.pending')
    try:
        if target.suffix.lower()=='.dwxml':
            pending.write_bytes(data)
        else:
            with zipfile.ZipFile(pending,'w',compression=zipfile.ZIP_DEFLATED) as z:
                if entries:
                    for info, original in entries:
                        z.writestr(info, data if info.filename==xml_name else original)
                    if not any(info.filename==xml_name for info,_ in entries):z.writestr(xml_name,data)
                else:
                    z.writestr(xml_name,data)
        if pending.stat().st_size > MAX_FILE:
            raise ValueError('原生输出超过25 MiB；保留临时输出，请显式选择 dwxmz 格式：'+str(pending))
        pending.replace(target)
    except Exception:
        # Preserve a failed export for diagnosis; never replace the source.
        raise


def _owned_annotations(metadata):
    return {'CHEMELLM-LAYOUT-'+g['id']+'-'+kind for g in metadata.get('groups',[])
            for kind in ('frame','title')}


def _protected_digest(root, owned):
    clone=copy.deepcopy(root)
    graphics=clone.find('GraphicObjects')
    for go in list(graphics):
        if go.findtext('Name') in owned:
            graphics.remove(go)
        else:
            for child in list(go):
                if child.tag in GRAPHIC_FIELDS.values():go.remove(child)
    dyn=clone.find('DynamicProperties')
    if dyn is not None:
        for p in list(dyn):
            if p.findtext('Name')==LAYOUT_KEY:dyn.remove(p)
        if not len(dyn) and not (dyn.text or '').strip():clone.remove(dyn)
    return hashlib.sha256(_compact(clone)).hexdigest()


def layout_flowsheet(request: DwsimInput, folder: Path, target: Path) -> dict:
    """Inspect -> place -> native edit -> whitelist merge -> actual saved reload."""
    from app.dwsim_layout import LayoutOptions, compile_layout, quality
    source=Path(request.flowsheet_path)
    if source.resolve()==target.resolve():
        raise ValueError('布局输出不得覆盖输入文件')
    before_request=DwsimInput(action='inspect',view='layout',flowsheet_path=str(source),timeout_seconds=request.timeout_seconds)
    before=invoke(before_request,folder/'before')['graphics']
    plan=compile_layout(before,request.layout or LayoutOptions())
    edited=invoke(DwsimInput(action='layout',flowsheet_path=str(source),timeout_seconds=request.timeout_seconds),
                  folder/'edit',native_layout=plan)
    root,xml_name,entries=read_native(source)
    previous_owned=_owned_annotations(before.get('metadata',{}))
    new_owned=_owned_annotations(plan)
    protected=_protected_digest(root,previous_owned)
    graphics=root.find('GraphicObjects');by_id={g.findtext('Name'):g for g in graphics}
    sim_ids={s.findtext('Name') for s in root.find('SimulationObjects')}
    if len(by_id)!=len(graphics):raise ValueError('原生图形名称重复，不能安全编辑')
    for ident in new_owned:
        if ident in by_id and ident not in previous_owned or ident in sim_ids:
            raise ValueError('分区图形名称与已有对象冲突：'+ident)
    for update in edited['graphic_updates']:
        go=by_id[update['name']]
        for key,native in GRAPHIC_FIELDS.items():
            if key in update:
                child=go.find(native)
                if child is None:child=ET.SubElement(go,native)
                value=update[key]
                child.text=str(value).lower() if isinstance(value,bool) else format(value,'.9g')
    for go in list(graphics):
        if go.findtext('Name') in previous_owned:graphics.remove(go)
    # Native frame/title objects serialize their own supported format.
    for i,xml in enumerate(edited['annotations']):
        graphics.insert(i,SafeET.fromstring(xml))
    dynamic=root.find('DynamicProperties')
    if dynamic is None:dynamic=ET.SubElement(root,'DynamicProperties')
    for prop in list(dynamic):
        if prop.findtext('Name')==LAYOUT_KEY:dynamic.remove(prop)
    prop=ET.SubElement(dynamic,'Property')
    ET.SubElement(prop,'Name').text=LAYOUT_KEY
    ET.SubElement(prop,'PropertyType').text='System.String'
    ET.SubElement(prop,'Data').text=json.dumps(json.dumps(plan,ensure_ascii=False),ensure_ascii=False)
    after_digest=_protected_digest(root,new_owned)
    if protected!=after_digest:
        raise ValueError('布局编辑改变了受保护的工艺或连接数据，拒绝保存')
    write_native(root,target,xml_name,entries)
    after=invoke(DwsimInput(action='inspect',view='layout',flowsheet_path=str(target),timeout_seconds=request.timeout_seconds),folder/'after')
    graph=after['graphics']
    actual={n['name']:n for n in graph['nodes']}
    for n in plan['nodes']:
        for field in GRAPHIC_FIELDS:
            if field in n and abs(float(actual[n['name']][field])-float(n[field]))>1e-3:
                raise ValueError('保存重开后的图形位置/方向不一致：'+n['name']+'/'+field)
    if graph['metadata']!=plan:
        raise ValueError('保存重开后布局元数据不一致')
    before_quality,after_quality=quality(before),quality(graph)
    report=dict(engine='DWSIM',action='layout',version=after['version'],solver_executed=False,
                saved_flowsheet=str(target),runner_sha256=after['runner_sha256'],
                layout_quality={'before':before_quality,'after':after_quality},
                protected_data_unchanged=True,protected_sha256=protected,saved_reload_verified=True,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                graphics=graph,before_graphics=before,previews=graph['previews'])
    report_path=folder/'DWSIM布局报告.json'
    report['layout_path']=str(report_path)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return report


def catalog(work_dir: Path, *, query='', offset=0, limit=30, timeout=120) -> dict:
    result = invoke(DwsimInput(action='catalog', timeout_seconds=timeout), work_dir/uuid.uuid4().hex)
    compounds = [item for item in result['compounds'] if query.casefold() in
                 ' '.join(str(v) for v in item.values()).casefold()]
    compounds.sort(key=lambda item: item['name'])
    result.update(compounds=compounds[offset:offset+limit], total_compounds=len(compounds), offset=offset,
                  next_offset=offset+limit if offset+limit < len(compounds) else None)
    return result


def run_flowsheet(flowsheet: str | Path, temp_dir: str | Path, timeout: int = 900) -> dict:
    """Compatibility helper used by smoke checks; always save to a new model."""
    work = Path(temp_dir).resolve()/uuid.uuid4().hex
    return invoke(DwsimInput(action='run', flowsheet_path=str(Path(flowsheet).resolve()), timeout_seconds=timeout),
                  work, save_path=work/'solved.dwxmz')


def streams_csv(result: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(['tag', 'name', 'type', 'temperature_K', 'pressure_Pa', 'mass_flow_kg_s', 'compounds', 'mole_fractions'])
    for stream in result.get('streams') or []:
        writer.writerow([*(stream.get(key, '') for key in ('tag', 'name', 'type', 'temperature_k', 'pressure_pa', 'mass_flow_kg_s')),
                         '；'.join(stream.get('compounds') or []),
                         '；'.join(str(value) for value in stream.get('mole_fractions') or [])])
    return buffer.getvalue()
