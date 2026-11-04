"""Explicit engineering specifications compiled to verified DWSIM 9.x members."""
from typing import Literal
from pydantic import Field, model_validator
from app.schemas import Inputs


class ProductSpec(Inputs):
    type: Literal['Stream_Ratio', 'Product_Molar_Flow_Rate', 'Product_Mass_Flow_Rate',
                  'Component_Fraction', 'Component_Recovery', 'Temperature', 'Heat_Duty']
    value: float
    unit: str = Field(description='DWSIM 规格单位；摩尔分数用 Molar，质量分数用 Mass')
    component: str = ''

    @model_validator(mode='after')
    def valid(self):
        if self.type.startswith('Component_') and not self.component:raise ValueError('组分产品规格需要 component')
        if self.type in {'Stream_Ratio','Product_Molar_Flow_Rate','Product_Mass_Flow_Rate','Temperature'} and self.value<=0:
            raise ValueError('塔产品规格必须为正')
        return self


class ColumnSpec(Inputs):
    stages: int = Field(ge=3, le=300)
    top_pressure_pa: float = Field(gt=0)
    bottom_pressure_pa: float = Field(gt=0)
    feed_stages: dict[str, int] = Field(description='物流位号到级号，1起始且包含冷凝器与再沸器')
    distillate_stream: str
    bottoms_stream: str
    condenser: Literal['Total_Condenser', 'Partial_Condenser', 'Full_Reflux'] = 'Total_Condenser'
    condenser_spec: ProductSpec
    reboiler_spec: ProductSpec
    stage_efficiency: float = Field(default=1, gt=0, le=1)
    maximum_iterations: int = Field(default=300,ge=1,le=10000)
    solver_tolerance: float = Field(default=1e-6,gt=0,le=.01)
    external_tolerance: float = Field(default=1e-5,gt=0,le=.01)

    @model_validator(mode='after')
    def valid(self):
        if any(not 1 <= s <= self.stages for s in self.feed_stages.values()):
            raise ValueError('进料级超出塔级范围')
        if self.bottom_pressure_pa < self.top_pressure_pa:
            raise ValueError('塔底压力不能小于塔顶压力')
        if self.distillate_stream==self.bottoms_stream:raise ValueError('塔顶和塔底产品物流不能相同')
        if self.condenser_spec.type.startswith('Product_') and self.reboiler_spec.type.startswith('Product_'):
            raise ValueError('两端同时指定产品流量构成已知规格冲突，请用独立的回流或产品规格')
        return self


class UnitSpec(Inputs):
    mode: Literal['outlet_temperature','heat_duty','outlet_pressure','pressure_change','ua',
                  'hot_outlet_temperature','cold_outlet_temperature','adiabatic','tp',
                  'column','shortcut','split_ratios','recycle','reaction']
    temperature_k: float | None = Field(default=None, gt=0)
    pressure_pa: float | None = Field(default=None, gt=0)
    pressure_change_pa: float | None = None
    pressure_drop_pa: float | None = Field(default=None, ge=0)
    heat_duty_kw: float | None = None
    efficiency: float | None = Field(default=None, gt=0, le=1)
    area_m2: float | None = Field(default=None, gt=0)
    overall_coefficient_w_m2_k: float | None = Field(default=None, gt=0)
    hot_pressure_drop_pa: float | None = Field(default=None, ge=0)
    cold_pressure_drop_pa: float | None = Field(default=None, ge=0)
    flow_direction: Literal['CounterCurrent','CoCurrent'] = 'CounterCurrent'
    column: ColumnSpec | None = None
    light_key: str | None = None
    heavy_key: str | None = None
    light_key_in_bottoms: float | None = Field(default=None, gt=0, lt=1)
    heavy_key_in_distillate: float | None = Field(default=None, gt=0, lt=1)
    reflux_ratio: float | None = Field(default=None, gt=0)
    ratios: list[float] | None = Field(default=None, min_length=2, max_length=3)
    maximum_iterations: int | None = Field(default=None, ge=1, le=10000)
    acceleration: Literal['None','Wegstein','Dominant_Eigenvalue','GlobalBroyden'] = 'None'
    temperature_tolerance_k: float | None = Field(default=None, gt=0)
    pressure_tolerance_pa: float | None = Field(default=None, gt=0)
    mass_flow_tolerance_kg_s: float | None = Field(default=None, gt=0)
    composition_tolerance: float | None = Field(default=None, gt=0, lt=1)
    reaction_set: str | None = None
    reactor_mode: Literal['Adiabatic','Isothermic','OutletTemperature'] = 'Adiabatic'


class Interaction(Inputs):
    compound1: str
    compound2: str
    model: Literal['PR','SRK','NRTL','UNIQUAC']
    parameters: dict[str, float] = Field(min_length=1, max_length=12,
                                        description='已通过 inspect 返回的参数成员；包括温度相关系数，不接收表达式')
    source: str = Field(min_length=1)
    conditions: str = Field(min_length=1)
    notes: str = ''


class PropertyPackage(Inputs):
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    name: str = Field(min_length=1)
    rationale: str = ''
    source: str = ''
    conditions: str = ''
    interactions: list[Interaction] = Field(default_factory=list, max_length=300)


class Reaction(Inputs):
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    type: Literal['Conversion','Equilibrium']
    stoichiometry: dict[str, float] = Field(min_length=2, max_length=100)
    base_compound: str
    phase: Literal['Vapor','Liquid','Mixture']
    conversion: float | None = Field(default=None, ge=0, le=1)
    equilibrium: Literal['Gibbs','Constant'] = 'Gibbs'
    equilibrium_constant: float | None = Field(default=None, gt=0)
    temperature_min_k: float = Field(default=1, gt=0)
    temperature_max_k: float = Field(default=2000, gt=0)
    source: str = ''
    conditions: str = ''

    @model_validator(mode='after')
    def valid(self):
        if self.stoichiometry.get(self.base_compound, 0) >= 0 or not any(v > 0 for v in self.stoichiometry.values()):
            raise ValueError('基准反应物系数须为负，且至少有一种产物')
        if self.type == 'Conversion' and self.conversion is None:
            raise ValueError('转化率反应必须显式填写 conversion')
        if self.type == 'Equilibrium' and self.conversion is not None:
            raise ValueError('平衡反应不能指定转化率')
        if self.equilibrium == 'Constant' and self.equilibrium_constant is None:
            raise ValueError('常数平衡模型需要 equilibrium_constant')
        if self.temperature_max_k <= self.temperature_min_k:
            raise ValueError('反应适用温度范围无效')
        return self


class ReactionSet(Inputs):
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    reactions: list[str] = Field(min_length=1, max_length=100, description='按列表次序配置反应顺序')


def compile_spec(obj):
    """Scalar mapping is deliberately finite; advanced collections are handled in the runner."""
    spec = obj.spec
    if spec is None: return {}
    typ, mode = obj.type, spec.mode
    p = {}
    def required(name):
        v = getattr(spec, name)
        if v is None: raise ValueError(obj.tag+' 缺少规格 '+name)
        return v
    def setp(name, field, factor=1): p[name] = required(field)*factor
    if typ in {'Heater','Cooler'}:
        if mode == 'outlet_temperature': p['CalcMode']='OutletTemperature'; setp('OutletTemperature','temperature_k')
        elif mode == 'heat_duty': p['CalcMode']='HeatAdded' if typ=='Heater' else 'HeatRemoved'; setp('HeatDuty','heat_duty_kw')
        else: raise ValueError(obj.tag+' 的加热/冷却模式无效')
        if spec.pressure_drop_pa is not None: p['PressureDrop']=spec.pressure_drop_pa
        if spec.efficiency is not None: p['Efficiency']=spec.efficiency*100
    elif typ in {'Pump','Compressor','Valve'}:
        if mode == 'outlet_pressure':
            p['CalcMode']='OutletPressure'; setp('Pout' if typ=='Pump' else 'OutletPressure' if typ=='Valve' else 'POut','pressure_pa')
        elif mode == 'pressure_change':
            p['CalcMode']='DeltaP' if typ=='Valve' else 'Delta_P'
            setp('PressureDrop' if typ=='Valve' else 'PressureIncrease' if typ=='Pump' else 'DeltaP','pressure_change_pa')
        else: raise ValueError(obj.tag+' 的压力规格模式无效')
        if typ!='Valve': setp('Efficiency' if typ=='Pump' else 'AdiabaticEfficiency','efficiency',100)
    elif typ=='HeatExchanger':
        if mode=='ua':
            p['CalculationMode']='CalcBothTemp_UA'; setp('Area','area_m2'); setp('OverallCoefficient','overall_coefficient_w_m2_k')
        elif mode in {'hot_outlet_temperature','cold_outlet_temperature'}:
            p['CalculationMode']='CalcTempColdOut' if mode=='hot_outlet_temperature' else 'CalcTempHotOut'
            setp('HotSideOutletTemperature' if mode=='hot_outlet_temperature' else 'ColdSideOutletTemperature','temperature_k')
        else: raise ValueError(obj.tag+' 的换热器模式无效')
        p['FlowDir']=spec.flow_direction; p['IgnoreLMTDError']=False
        if spec.hot_pressure_drop_pa is not None: p['HotSidePressureDrop']=spec.hot_pressure_drop_pa
        if spec.cold_pressure_drop_pa is not None: p['ColdSidePressureDrop']=spec.cold_pressure_drop_pa
    elif typ=='Vessel':
        if mode=='adiabatic': p['CalculationMode']='Adiabatic'
        elif mode=='tp': p['CalculationMode']='Legacy'; p['OverrideP']=True; p['OverrideT']=True; setp('FlashPressure','pressure_pa'); setp('FlashTemperature','temperature_k')
        else: raise ValueError(obj.tag+' 的闪蒸模式无效')
    elif typ=='DistillationColumn':
        if mode!='column' or spec.column is None: raise ValueError(obj.tag+' 需要 column 配置')
    elif typ=='ShortcutColumn':
        if mode!='shortcut': raise ValueError(obj.tag+' 需要 shortcut 模式')
        for n in ('light_key','heavy_key','light_key_in_bottoms','heavy_key_in_distillate','reflux_ratio','pressure_pa'): required(n)
    elif typ=='Splitter':
        if mode!='split_ratios' or spec.ratios is None or abs(sum(spec.ratios)-1)>1e-8 or any(v<0 for v in spec.ratios):
            raise ValueError(obj.tag+' 分流比必须非负且和为1')
        p['OperationMode']='SplitRatios'
    elif typ=='OT_Recycle':
        if mode!='recycle': raise ValueError(obj.tag+' 需要 recycle 模式')
        setp('MaximumIterations','maximum_iterations'); p['AccelerationMethod']=spec.acceleration
    elif typ in {'RCT_Conversion','RCT_Equilibrium','Reactor_Conversion','Reactor_Equilibrium'}:
        if mode!='reaction': raise ValueError(obj.tag+' 需要 reaction 模式')
        required('reaction_set'); p['ReactorOperationMode']=spec.reactor_mode
        if spec.reactor_mode=='OutletTemperature':setp('OutletTemperature','temperature_k')
        if spec.pressure_drop_pa is not None:p['DeltaP']=spec.pressure_drop_pa
    else: raise ValueError('该单元尚无结构化规格：'+typ)
    conflicts=set(p)&set(obj.parameters)
    if conflicts or obj.properties:
        raise ValueError(obj.tag+' 结构化规格不能与同对象的原生属性混用；冲突参数：'+','.join(conflicts))
    # Reject redundant thermodynamic targets rather than silently discard them.
    target_fields={'temperature_k','pressure_pa','pressure_change_pa','heat_duty_kw','area_m2','overall_coefficient_w_m2_k'}
    allowed={'temperature_k'} if mode in {'outlet_temperature','hot_outlet_temperature','cold_outlet_temperature'} else {
        'heat_duty':{'heat_duty_kw'},'outlet_pressure':{'pressure_pa'},'pressure_change':{'pressure_change_pa'},
        'ua':{'area_m2','overall_coefficient_w_m2_k'},'tp':{'temperature_k','pressure_pa'},'shortcut':{'pressure_pa'},'reaction':{'temperature_k'}}.get(mode,set())
    if any(getattr(spec,k) is not None for k in target_fields-allowed):raise ValueError(obj.tag+' 包含与所选模式冲突的额外规格')
    used=allowed|{'mode'}
    if typ in {'Heater','Cooler'}:used|={'pressure_drop_pa','efficiency'}
    if typ in {'Pump','Compressor'}:used|={'efficiency'}
    if typ=='HeatExchanger':used|={'hot_pressure_drop_pa','cold_pressure_drop_pa','flow_direction'}
    if typ=='DistillationColumn':used|={'column'}
    if typ=='ShortcutColumn':used|={'light_key','heavy_key','light_key_in_bottoms','heavy_key_in_distillate','reflux_ratio'}
    if typ=='Splitter':used|={'ratios'}
    if typ=='OT_Recycle':used|={'maximum_iterations','acceleration','temperature_tolerance_k','pressure_tolerance_pa','mass_flow_tolerance_kg_s','composition_tolerance'}
    if mode=='reaction':
        used|={'reaction_set','reactor_mode','pressure_drop_pa'}
        if spec.reactor_mode!='OutletTemperature' and spec.temperature_k is not None:raise ValueError(obj.tag+' 仅指定出口温度模式接受 temperature_k')
    extra=[k for k,f in UnitSpec.model_fields.items() if k not in used and getattr(spec,k) is not None and getattr(spec,k)!=f.default]
    if extra:raise ValueError(obj.tag+' 所选模式不使用这些规格：'+','.join(extra))
    return p
