"""Public schemas for bounded steady-state studies and explicit engineering publication."""
from typing import Literal
from pydantic import Field, model_validator
from app.schemas import Inputs
from app.dwsim import DwsimObject, BalanceOptions
from app.dwsim_specs import PropertyPackage


class Metric(Inputs):
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    path: str = Field(min_length=1,max_length=500,description='JSON Pointer，可用 @位号选择数组对象，例如 /streams/@P/temperature_k')
    unit: str = Field(min_length=1,max_length=80)
    denominator_path: str | None = Field(default=None,max_length=500,description='可选分母指针，用于收率/回收率等比值；两端必须同量纲')


class Case(Inputs):
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    updates: list[DwsimObject] = Field(default_factory=list,max_length=200)
    property_packages: list[PropertyPackage] = Field(default_factory=list,max_length=20)


class Scan(Inputs):
    tag: str
    field: Literal['temperature_k','pressure_pa','mass_flow_kg_s','reflux_ratio']
    values: list[float] = Field(min_length=1,max_length=50)
    object_template: DwsimObject | None = Field(default=None,description='回流比扫描提供完整塔规格；仅替换冷凝器 Stream_Ratio 数值')

    @model_validator(mode='after')
    def valid(self):
        if any(v<0 if self.field=='mass_flow_kg_s' else v<=0 for v in self.values):raise ValueError('扫描值超出物理范围')
        if self.field=='reflux_ratio':
            o=self.object_template
            if not o or o.tag!=self.tag or o.type!='DistillationColumn' or not o.spec or not o.spec.column or o.spec.column.condenser_spec.type!='Stream_Ratio':
                raise ValueError('回流比扫描须提供同位号严格塔 object_template，冷凝器规格为 Stream_Ratio')
        elif self.object_template:raise ValueError('物流扫描不使用 object_template')
        return self


class StudyInput(Inputs):
    action: Literal['start','status','resume','cancel']
    job_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    request_key: str = Field(default='',max_length=100,pattern=r'^[A-Za-z0-9_-]*$')
    flowsheet_path: str | None = None
    cases: list[Case] = Field(default_factory=list,max_length=50)
    scan: Scan | None = None
    metrics: list[Metric] = Field(default_factory=list,max_length=30)
    timeout_seconds: int = Field(default=120,ge=5,le=900)
    validation_options: BalanceOptions = Field(default_factory=BalanceOptions)
    basis_keys: list[str] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def valid(self):
        if self.action=='start':
            if not self.flowsheet_path or bool(self.cases)==bool(self.scan):raise ValueError('start 需要固定原生模型及 cases/scan 二选一')
            if len({c.key for c in self.cases})!=len(self.cases):raise ValueError('工况 key 重复')
            if not self.metrics:raise ValueError('至少填写一个有单位的比较指标')
        elif not self.job_id:raise ValueError('status/resume/cancel 需要 job_id')
        if len({m.key for m in self.metrics})!=len(self.metrics):raise ValueError('指标 key 重复')
        return self


class QuantityMapping(Metric):
    origin: Literal['operating','simulation_load'] = 'operating'
    conditions: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class PublishObject(Inputs):
    kind: Literal['stream','unit_operation','equipment','utility']
    key: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    title: str = Field(min_length=1,max_length=200)
    expected_revision: int = Field(ge=0)
    quantities: list[QuantityMapping] = Field(min_length=1,max_length=100)


class ReferenceValue(Metric):
    value: float
    source: str = Field(min_length=1)
    conditions: str = Field(min_length=1)
    artifact_id: str | None = None
    page: int | None = Field(default=None,ge=1)
    relative_tolerance: float | None = Field(default=None,gt=0)
    absolute_tolerance: float | None = Field(default=None,ge=0)


class PublishInput(Inputs):
    calculation_id: str
    objects: list[PublishObject] = Field(default_factory=list,max_length=100)
    references: list[ReferenceValue] = Field(default_factory=list,max_length=100)
    case: str = Field(min_length=1,max_length=200)

    @model_validator(mode='after')
    def valid(self):
        if not self.objects and not self.references:raise ValueError('至少提供工程对象映射或参考值')
        if len({(o.kind,o.key) for o in self.objects})!=len(self.objects):raise ValueError('发布对象重复')
        for o in self.objects:
            if len({q.key for q in o.quantities})!=len(o.quantities):raise ValueError('对象内数值 key 重复')
        return self


def resolve(result, path):
    if not path.startswith('/'):raise ValueError('结果路径必须是 JSON Pointer：'+path)
    node=result; actual=[]
    for part in path.split('/')[1:]:
        key=part.replace('~1','/').replace('~0','~')
        if isinstance(node,list):
            if key.startswith('@'):
                matches=[i for i,v in enumerate(node) if isinstance(v,dict) and v.get('tag')==key[1:]]
                if len(matches)!=1:raise ValueError('结果位号不存在或不唯一：'+key)
                key=matches[0]
            else:
                try:key=int(key)
                except ValueError as exc:raise ValueError('数组下标无效：'+path) from exc
                if key<0:raise ValueError('数组下标不能为负')
        try:node=node[key]
        except (KeyError,IndexError,TypeError) as exc:raise ValueError('结果路径不存在：'+path) from exc
        actual.append(str(key).replace('~','~0').replace('/','~1'))
    if not path.startswith('/') or isinstance(node,bool) or not isinstance(node,(int,float)):
        raise ValueError('结果路径必须指向数值：'+path)
    import math
    if not math.isfinite(node):raise ValueError('结果不是有限数值')
    return node,'/'+'/'.join(actual)


def native_unit(result, path):
    """Only units with an explicit engine contract may be used for engineering values."""
    parts=path.split('/')[1:]
    if len(parts)<3:raise ValueError('结果位置没有已知单位：'+path)
    root,index,field=parts[:3]
    stream_units={'temperature_k':'K','pressure_pa':'Pa','mass_flow_kg_s':'kg/s',
        'molar_flow_mol_s':'mol/s','specific_enthalpy_kj_kg':'kJ/kg','enthalpy_flow_kw':'kW',
        'total_enthalpy_flow_kw':'kW','mole_fractions':'dimensionless',
        'component_mass_flows_kg_s':'kg/s','molecular_weights_g_mol':'g/mol','element_flows_mol_s':'mol/s'}
    if root=='streams' and field in stream_units:return stream_units[field]
    if root=='energy_streams' and field=='energy_flow_kw':return 'kW'
    if root=='unit_operations':
        if field=='net_energy_input_kw':return 'kW'
        if field=='properties' and len(parts)==5 and parts[-1]=='value':
            unit=result[root][int(index)]['properties'][int(parts[3])].get('unit')
            if unit:return unit
    raise ValueError('结果位置没有已知单位；请选物流字段、热流或带原生单位的单元属性：'+path)


def convert_value(value, source, target):
    from app.design_data import units
    try:return float(units.Quantity(value,source).to(target).magnitude)
    except Exception as exc:raise ValueError('单位不兼容或无法解析：'+source+' -> '+target) from exc


def metric_value(result, metric):
    value,path=resolve(result,metric.path)
    source=native_unit(result,path)
    if metric.denominator_path:
        denominator,denominator_path=resolve(result,metric.denominator_path)
        denominator=convert_value(denominator,native_unit(result,denominator_path),source)
        if denominator==0:raise ValueError('指标分母为零：'+metric.key)
        value/=denominator
        source='dimensionless'
    return convert_value(value,source,metric.unit),path
