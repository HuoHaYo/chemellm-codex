"""Validated, model-independent requests for the external DWSIM engine."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from app.schemas import Inputs


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

    @model_validator(mode='after')
    def graph(self):
        if len(set(self.compounds)) != len(self.compounds) or any(not name.strip() for name in self.compounds):
            raise ValueError('组分名称不能重复或为空')
        tags = {obj.tag for obj in self.objects}
        if len(tags) != len(self.objects):
            raise ValueError('设备/物流位号重复')
        for obj in self.objects:
            if obj.type == 'MaterialStream':
                if obj.mole_fractions is not None and set(obj.mole_fractions) != set(self.compounds):
                    raise ValueError('组成必须逐一列出模型中的全部组分：'+obj.tag)
            elif any(getattr(obj, field) is not None for field in ('temperature_k', 'pressure_pa', 'mass_flow_kg_s', 'mole_fractions')):
                raise ValueError('温压流量和组成字段仅用于 MaterialStream：'+obj.tag)
        inputs, outputs = set(), set()
        for connection in self.connections:
            if connection.source not in tags or connection.target not in tags or connection.source == connection.target:
                raise ValueError('连接对象不存在或自连接')
            incoming, outgoing = (connection.target, connection.target_port), (connection.source, connection.source_port)
            if incoming in inputs or outgoing in outputs:
                raise ValueError('同一端口不能重复连接')
            inputs.add(incoming)
            outputs.add(outgoing)
        # Feed streams must have explicit state; avoid DWSIM's demonstration defaults.
        targets = {connection.target for connection in self.connections}
        for obj in self.objects:
            if obj.type == 'MaterialStream' and obj.tag not in targets and any(
                    getattr(obj, field) is None for field in ('temperature_k', 'pressure_pa', 'mass_flow_kg_s', 'mole_fractions')):
                raise ValueError('进料须显式指定温度、压力、质量流量和组成：'+obj.tag)
        return self


class DwsimInput(Inputs):
    action: Literal['status', 'catalog', 'inspect', 'create', 'run'] = 'status'
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

    @model_validator(mode='after')
    def inputs(self):
        if self.action == 'create':
            if self.model is None or self.flowsheet_path is not None or self.updates:
                raise ValueError('create 需要 model，不接受 flowsheet_path/updates')
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
