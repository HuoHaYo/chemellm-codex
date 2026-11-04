"""Validated, model-independent requests for the external DWSIM engine."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from app.schemas import Inputs
from app.dwsim_specs import UnitSpec, PropertyPackage, Reaction, ReactionSet, compile_spec


class BalanceOptions(Inputs):
    relative: float = Field(default=1e-5, gt=0, le=.1)
    mass_absolute_kg_s: float = Field(default=1e-8, gt=0)
    component_absolute_kg_s: float = Field(default=1e-8, gt=0)
    energy_absolute_kw: float = Field(default=1e-4, gt=0)
    element_absolute_mol_s: float = Field(default=1e-7, gt=0)


class DwsimObject(Inputs):
    tag: str = Field(min_length=1, max_length=100)
    type: str = Field(default='MaterialStream', min_length=1, max_length=80,
                      description='DWSIM ObjectType 名称；catalog 返回可用名称')
    x: int = Field(default=100, ge=-100000, le=100000)
    y: int = Field(default=100, ge=-100000, le=100000)
    temperature_k: float | None = Field(default=None, gt=0, le=10000)
    pressure_pa: float | None = Field(default=None, gt=0, le=1e10)
    mass_flow_kg_s: float | None = Field(default=None, ge=0, le=1e10)
    mole_fractions: dict[str, float] | None = Field(default=None,
        description='组分名称到摩尔分数，明确提供全部组分，和为1；不自动归一化')
    properties: dict[str, float | bool] = Field(default_factory=dict,
        description='inspect 返回的可写 DWSIM 属性 ID；单位见 inspect 的 unit（SI）')
    parameters: dict[str, str | float | bool] = Field(default_factory=dict,
        description='inspect 返回的模型标量参数，例如 CalcMode=OutletTemperature；单位遵循 DWSIM 原生 API')
    spec: UnitSpec | None = None
    property_package: str | None = Field(default=None, description='模型 property_packages 的稳定 key 或原生模型的物性包 ID')

    @model_validator(mode='after')
    def finite(self):
        for field in ('temperature_k', 'pressure_pa', 'mass_flow_kg_s'):
            value = getattr(self, field)
            if value is not None and not math.isfinite(value):
                raise ValueError(field+' 必须是有限数值')
        if self.mole_fractions is not None:
            values = list(self.mole_fractions.values())
            if not values or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values) or not math.isclose(sum(values), 1, abs_tol=1e-8, rel_tol=0):
                raise ValueError('摩尔分数必须有限、非负且和为1')
        if len(self.properties) > 100 or len(self.parameters) > 100:
            raise ValueError('每个对象最多100个属性/参数')
        for values in (self.properties, self.parameters):
            if any(isinstance(v, float) and not math.isfinite(v) for v in values.values()):
                raise ValueError('参数必须是有限数值')
        compile_spec(self)
        return self


class DwsimConnection(Inputs):
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    source_port: int = Field(default=0, ge=0, le=100)
    target_port: int = Field(default=0, ge=0, le=100)


class DwsimModel(Inputs):
    compounds: list[str] = Field(min_length=1, max_length=100)
    property_package: str = Field(min_length=1, max_length=200,
        description='catalog 返回的准确名称；按体系选择，不能以默认值代替物性依据')
    objects: list[DwsimObject] = Field(min_length=1, max_length=200)
    connections: list[DwsimConnection] = Field(default_factory=list, max_length=500)
    property_packages: list[PropertyPackage] = Field(default_factory=list, max_length=20)
    reactions: list[Reaction] = Field(default_factory=list, max_length=100)
    reaction_sets: list[ReactionSet] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def graph(self):
        if len(set(self.compounds)) != len(self.compounds) or any(not name.strip() for name in self.compounds):
            raise ValueError('组分名称不能重复或为空')
        tags = {obj.tag for obj in self.objects}
        for items in (self.property_packages,self.reactions,self.reaction_sets):
            if len({x.key for x in items})!=len(items): raise ValueError('物性包、反应或反应集 key 重复')
        reactions={r.key:r for r in self.reactions}
        for r in self.reactions:
            if set(r.stoichiometry)-set(self.compounds):raise ValueError('反应含未选组分：'+r.key)
        for s in self.reaction_sets:
            if set(s.reactions)-reactions.keys():raise ValueError('反应集引用不存在的反应：'+s.key)
        if len(tags) != len(self.objects):
            raise ValueError('设备/物流位号重复')
        for obj in self.objects:
            if obj.type == 'MaterialStream':
                if obj.mole_fractions is not None and set(obj.mole_fractions) != set(self.compounds):
                    raise ValueError('组成必须逐一列出模型中的全部组分：'+obj.tag)
            elif any(getattr(obj, field) is not None for field in ('temperature_k', 'pressure_pa', 'mass_flow_kg_s', 'mole_fractions')):
                raise ValueError('温压流量和组成字段仅用于 MaterialStream：'+obj.tag)
            if obj.spec and obj.spec.column:
                c=obj.spec.column
                if (set(c.feed_stages)|{c.distillate_stream,c.bottoms_stream})-tags:
                    raise ValueError('塔规格引用不存在的物流：'+obj.tag)
                if {c.condenser_spec.component,c.reboiler_spec.component}-{''}-set(self.compounds):
                    raise ValueError('塔产品规格引用未选组分：'+obj.tag)
            if obj.spec and obj.spec.reaction_set and obj.spec.reaction_set not in {r.key for r in self.reaction_sets}:
                raise ValueError('单元引用不存在的反应集：'+obj.tag)
        for pp in self.property_packages:
            for ip in pp.interactions:
                if ip.compound1==ip.compound2 or {ip.compound1,ip.compound2}-set(self.compounds):
                    raise ValueError('二元参数引用无效组分：'+pp.key)
        inputs, outputs = set(), set()
        for connection in self.connections:
            if connection.source not in tags or connection.target not in tags or connection.source == connection.target:
                raise ValueError('连接对象不存在或自连接')
            incoming, outgoing = (connection.target, connection.target_port), (connection.source, connection.source_port)
            if incoming in inputs or outgoing in outputs:
                raise ValueError('同一端口不能重复连接')
            inputs.add(incoming)
            outputs.add(outgoing)
        return self


class DwsimInput(Inputs):
    action: Literal['status', 'catalog', 'inspect', 'check', 'create', 'run'] = 'status'
    flowsheet_path: str | None = Field(default=None, min_length=1, max_length=4000)
    model: DwsimModel | None = None
    updates: list[DwsimObject] = Field(default_factory=list, max_length=200,
        description='载入模型后按位号或内部名称更新已有对象；type/x/y不改变现有对象')
    timeout_seconds: int = Field(default=120, ge=5, le=900)
    compound_query: str = Field(default='', max_length=200)
    object_tags: list[str] = Field(default_factory=list, max_length=10,
        description='inspect 指定对象位号或内部名称以展开全部属性；省略时返回对象/端口概览')
    property_offset: int = Field(default=0, ge=0)
    property_limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=30, ge=1, le=100)
    validation_options: BalanceOptions = Field(default_factory=BalanceOptions)
    basis_keys: list[str] = Field(default_factory=list, max_length=100,
                                 description='关联当前项目工程依据对象，例如 basis/feed')
    property_packages: list[PropertyPackage] = Field(default_factory=list, max_length=20,
        description='run/check 时增加或替换指定 key 的物性配置；不修改原件')
    initialization: list[list[DwsimObject]] = Field(default_factory=list,max_length=10,
        description='显式分步初始化，每步应用修改并求解；仅 run 可用，记录各步状态，最终再应用 updates')
    run_context: dict[str,str] = Field(default_factory=dict,max_length=10)

    @model_validator(mode='after')
    def inputs(self):
        if self.initialization and self.action!='run':raise ValueError('仅 run 接受分步初始化')
        if self.action == 'check':
            if (self.model is None) == (self.flowsheet_path is None) or self.model is not None and self.updates:
                raise ValueError('check 提供 model 或 flowsheet_path 二选一；载入文件可检查 updates')
        elif self.action == 'create':
            if self.model is None or self.flowsheet_path is not None or self.updates:
                raise ValueError('create 需要 model，不接受 flowsheet_path/updates')
            targets={c.target for c in self.model.connections}
            for o in self.model.objects:
                if o.type=='MaterialStream' and o.tag not in targets and any(getattr(o,k) is None for k in ['temperature_k','pressure_pa','mass_flow_kg_s','mole_fractions']):
                    raise ValueError('进料须显式指定温度、压力、质量流量和组成：'+o.tag)
        elif self.action in {'run', 'inspect'}:
            if self.flowsheet_path is None or self.model is not None:
                raise ValueError('run/inspect 需要 flowsheet_path，不接受 model')
            if self.action == 'inspect' and self.updates:
                raise ValueError('inspect 不修改模型；更新使用 run')
        elif self.model is not None or self.flowsheet_path is not None or self.updates:
            raise ValueError('status/catalog 不接受模型或修改参数')
        if len({obj.tag for obj in self.updates}) != len(self.updates):
            raise ValueError('更新对象位号重复')
        return self
