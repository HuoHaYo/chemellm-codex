"""Independent checks on explicit DWSIM data; missing data never counts as passed."""
from __future__ import annotations

import math
from app.dwsim import BalanceOptions


def model_preflight(model):
    """Check explicit request inputs before native defaults can hide missing data."""
    checks=[];by_tag={o.tag:o for o in model.objects}
    for edge in model.connections:
        a,b=by_tag[edge.source],by_tag[edge.target]
        streams={'MaterialStream','EnergyStream'}
        checks.append(finding('connection',a.tag+'->'+b.tag,(a.type in streams)!=(b.type in streams),
                              '连接须位于物流与单元之间',source_port=edge.source_port,target_port=edge.target_port))
    targets={e.target for e in model.connections}
    for o in model.objects:
        if o.type=='MaterialStream' and o.tag not in targets:
            missing=[k for k in ['temperature_k','pressure_pa','mass_flow_kg_s','mole_fractions'] if getattr(o,k) is None]
            checks.append(finding('feed_state',o.tag,not missing,'进料显式状态',missing=missing))
        elif o.type not in {'MaterialStream','EnergyStream','Mixer','Splitter','Tank','OT_Recycle'} and not o.spec:
            p=o.parameters
            needed={'Heater':('CalcMode',),'Cooler':('CalcMode',),'Pump':('CalcMode',),
                'Compressor':('CalcMode',),'Valve':('CalcMode',),'HeatExchanger':('CalculationMode',)}.get(o.type)
            if needed:
                if o.properties and needed[0] not in p:
                    checks.append(finding('specification',o.tag,None,'原生属性输入：模式和规格需载入后核对'));continue
                missing=[k for k in needed if k not in p]
                modes={'OutletTemperature':'OutletTemperature','HeatAdded':'HeatDuty','HeatRemoved':'HeatDuty',
                    'OutletPressure':'Pout' if o.type=='Pump' else 'POut' if o.type=='Compressor' else 'OutletPressure',
                    'CalcBothTemp_UA':'Area'}
                target=modes.get(p.get(needed[0]))
                if target and target not in p and not o.properties:missing.append(target)
                checks.append(finding('specification',o.tag,False if missing else True,'显式支持的原生规格检查',missing=missing))
            else:checks.append(finding('specification',o.tag,None,'尚未独立判断该单元的原生规格'))
    return checks


def finding(key, tag, passed, reason='', **values):
    return dict(key=key, tag=tag, status='passed' if passed is True else 'failed' if passed is False else 'not_checked',
                passed=passed, reason=reason, **values)


def preflight(result, basis_keys=(), objects=None):
    """Use the engine's actual connector types and effective scalar settings."""
    checks = []
    objects = objects or result.get('objects') or []
    by_tag = {o['tag']: o for o in objects}
    for link in result.get('connections', []):
        a, b = by_tag.get(link['source']), by_tag.get(link['target'])
        if a and b:
            streams = {'MaterialStream', 'EnergyStream'}
            valid = (a['type'] in streams) != (b['type'] in streams)
            if a['type'] == b['type'] == 'MaterialStream':
                valid = False
            checks.append(finding('connection', f"{a['tag']}->{b['tag']}", valid, '物料/能量连接应通过单元操作'))
    for obj in objects:
        tag, typ = obj['tag'], obj['type']
        if typ in {'MaterialStream', 'EnergyStream'}:
            if typ=='MaterialStream' and not any(e['target']==tag for e in result.get('connections',[])):
                values=[obj.get(k) for k in ['temperature_k','pressure_pa','mass_flow_kg_s']]
                composition=obj.get('mole_fractions')
                valid=all(isinstance(v,(float,int)) and math.isfinite(v) and v>=0 for v in values)
                valid=valid and bool(composition) and all(v is not None and math.isfinite(v) and 0<=v<=1 for v in composition) and abs(sum(composition)-1)<1e-8
                checks.append(finding('feed_state',tag,valid,'载入后的进料状态、组成；来源与显式输入依据另行核对'))
            continue
        supported={'Mixer','Splitter','Heater','Cooler','Pump','Compressor','Valve','HeatExchanger','Vessel',
                   'ShortcutColumn','DistillationColumn','Recycle','Reactor_Conversion','Reactor_Equilibrium'}
        if typ not in supported:
            checks.append(finding('unit_support',tag,None,'该单元不在独立规格与端口检查范围内'));continue
        incoming = [e for e in result.get('connections', []) if e['target'] == tag and by_tag.get(e['source'], {}).get('type') == 'MaterialStream']
        outgoing = [e for e in result.get('connections', []) if e['source'] == tag and by_tag.get(e['target'], {}).get('type') == 'MaterialStream']
        minimum = 2 if typ in {'HeatExchanger', 'Mixer'} else 1
        # A mixer with one feed is mathematically valid, though usually unhelpful.
        if typ == 'Mixer': minimum = 1
        outlets=2 if typ in {'HeatExchanger','Vessel','ShortcutColumn','DistillationColumn','Splitter','Reactor_Conversion','Reactor_Equilibrium'} else 1
        checks.append(finding('material_ports', tag, len(incoming) >= minimum and len(outgoing) >= outlets,
                              '检查已连接的物料入口和出口', inputs=len(incoming), outputs=len(outgoing)))
        if typ in {'Heater','Cooler','Pump','Compressor','ShortcutColumn'}:
            connected=[e for e in result.get('connections',[]) if (e['source']==tag and by_tag.get(e['target'],{}).get('type')=='EnergyStream') or (e['target']==tag and by_tag.get(e['source'],{}).get('type')=='EnergyStream')]
            checks.append(finding('energy_ports',tag,len(connected)>=(2 if typ=='ShortcutColumn' else 1),'必要热流或轴功连接',count=len(connected)))
        for link in result.get('connections',[]):
            if link['target']==tag:
                ports=obj.get('input_ports',[]);index=link.get('target_port')
                if isinstance(index,int) and 0<=index<len(ports):
                    energy=by_tag.get(link['source'],{}).get('type')=='EnergyStream'
                    connector_type=ports[index].get('type','')
                    checks.append(finding('port_type',tag,('Energy' in connector_type or connector_type=='ConEn')==energy,'实际连接器类型',port=index))
        spec=obj.get('settings',{}).get('structured_spec')
        if spec:
            from app.dwsim_specs import compile_spec
            from app.dwsim import DwsimObject
            scalar=compile_spec(DwsimObject(tag=tag,type={'Recycle':'OT_Recycle'}.get(typ,typ),spec=spec))
            changed=[key for key,value in scalar.items() if obj['settings'].get(key)!=value]
            checks.append(finding('specification',tag,not changed,'结构化规格与当前标量设置比较；仍须检查求解结果',mode=spec['mode'],changed=changed))
        elif obj.get('specification_checks'):
            checks.extend(obj['specification_checks'])
        else:
            settings=obj.get('settings',{})
            mode=settings.get('CalcMode')
            required={'OutletTemperature':['OutletTemperature'],'HeatAdded':['HeatDuty'],'HeatRemoved':['HeatDuty'],
                'OutletPressure':['Pout' if typ=='Pump' else 'POut' if typ=='Compressor' else 'OutletPressure']}.get(mode)
            if typ in {'Heater','Cooler','Pump','Compressor','Valve'} and required:
                bad=[key for key in required if not isinstance(settings.get(key),(float,int)) or not math.isfinite(settings[key]) or
                     (key!='HeatDuty' and settings[key]<=0)]
                checks.append(finding('specification',tag,not bad,'按当前有效模式核对必需数值；原生默认值来源待核对',mode=mode,invalid=bad))
            else:checks.append(finding('specification', tag, None, '未独立判断该原生单元的自由度；求解前核对模型规格'))
    checks.append(finding('basis', 'flowsheet', None if not basis_keys else True,
                          '未关联设计依据，计算可继续，使用结果前补充' if not basis_keys else '已关联项目依据', keys=list(basis_keys)))
    return {'ready_to_solve': not any(c['passed'] is False for c in checks), 'checks': checks,
            'scope': '连接与已支持规格检查；未求解，不代表完整自由度分析'}


def validate(result, options=None):
    options = options or BalanceOptions()
    checks = [finding('solver', 'flowsheet', bool(result.get('solved')) and not result.get('solver_errors') and not result.get('result_errors'),
                      '引擎求解与基本结果读取')]
    streams = {s['tag']: s for s in result.get('streams') or []}
    units = {s['tag']: s for s in result.get('unit_operations') or []}
    links = result.get('connections')
    if links is None or not streams:
        checks.append(finding('balances', 'flowsheet', None, '缺少连接或物流数据，不能独立校核'))
    else:
        def sum_field(tags, field):
            values = [streams[t].get(field) for t in tags]
            if any(v is None or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
                return None
            return sum(values)

        def balance(key, tag, a, b, absolute, unit):
            if a is None or b is None or not math.isfinite(a) or not math.isfinite(b):
                checks.append(finding(key, tag, None, '原生数据缺失或非有限值', unit=unit)); return
            residual = a-b
            tolerance = absolute + options.relative*max(abs(a), abs(b))
            checks.append(finding(key, tag, abs(residual) <= tolerance, '入口减出口',
                                  residual=residual, tolerance=tolerance, inlet=a, outlet=b, unit=unit))

        def boundary(tag, ins, outs, q, reactive=False):
            if not ins or not outs:
                checks.append(finding('boundary',tag,None,'缺少完整物料入口或出口'));return
            balance('mass', tag, sum_field(ins, 'mass_flow_kg_s'), sum_field(outs, 'mass_flow_kg_s'), options.mass_absolute_kg_s, 'kg/s')
            names = set().union(*(set(streams[t].get('component_mass_flows_kg_s', {})) for t in ins+outs))
            if not reactive:
                if not names: checks.append(finding('components', tag, None, '缺少组分质量流量'))
                for name in sorted(names):
                    def component(tags):
                        vals = [streams[t].get('component_mass_flows_kg_s', {}).get(name) for t in tags]
                        return None if any(v is None for v in vals) else sum(vals)
                    balance('component:'+name, tag, component(ins), component(outs), options.component_absolute_kg_s, 'kg/s')
            else:
                if any(streams[t].get('element_data_complete') is False for t in ins+outs):
                    checks.append(finding('element_data',tag,None,'存在元素组成不完整的组分'))
                elements = set().union(*(set(streams[t].get('element_flows_mol_s', {})) for t in ins+outs))
                if not elements: checks.append(finding('elements', tag, None, '缺少元素组成，不能检查反应守恒'))
                for name in sorted(elements):
                    def elem(tags):
                        vals = [streams[t].get('element_flows_mol_s', {}).get(name) for t in tags]
                        return None if any(v is None for v in vals) else sum(vals)
                    balance('element:'+name, tag, elem(ins), elem(outs), options.element_absolute_mol_s, 'mol/s')
            hin, hout = sum_field(ins, 'enthalpy_flow_kw'), sum_field(outs, 'enthalpy_flow_kw')
            if reactive:
                hin, hout = sum_field(ins, 'total_enthalpy_flow_kw'), sum_field(outs, 'total_enthalpy_flow_kw')
            balance('energy', tag, None if hin is None or q is None else hin+q, hout, options.energy_absolute_kw, 'kW')

        all_q = []
        if not units:checks.append(finding('units','flowsheet',None,'缺少单元结果'))
        for tag, unit in units.items():
            ins = [e['source'] for e in links if e['target'] == tag and e['source'] in streams]
            outs = [e['target'] for e in links if e['source'] == tag and e['target'] in streams]
            # Heat/work inferred only from native unit settings, never from the enthalpy residual being tested.
            q = unit.get('net_energy_input_kw')
            boundary(tag, ins, outs, q, unit.get('reactive', False))
            all_q.append(q)
        ins = [t for t in streams if not any(e['target'] == t for e in links)]
        outs = [t for t in streams if not any(e['source'] == t for e in links)]
        if not ins or not outs:
            checks.append(finding('boundary', 'flowsheet', None, '不能识别完整外部物料边界'))
        else:
            boundary('flowsheet', ins, outs, None if any(q is None for q in all_q) else sum(all_q), any(u.get('reactive') for u in units.values()))
    statuses = [c['passed'] for c in checks]
    return {'schema_version':1,'passed': False if False in statuses else None if None in statuses else True,
            'scope': '求解状态、质量/组分或元素/能量守恒；不代表物性适用性或专业审核',
            'checks': checks, 'tolerances': options.model_dump()}
