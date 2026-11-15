"""Explicit wiring inputs for QElectroTech 0.100 native projects.

Drawing coordinates are QET graphic units, never inferred process dimensions.
"""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from app.schemas import Inputs


class Terminal(Inputs):
    id: str = Field(min_length=1, max_length=40)
    side: Literal['left', 'right', 'top', 'bottom'] = 'left'
    potential: str = Field(default='', max_length=80, description='已知同电位网名；未知留空，不从位号推断')


class Component(Inputs):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(default='', max_length=160)
    kind: Literal['device', 'instrument', 'plc', 'terminal', 'relay', 'contact_no', 'contact_nc', 'power'] = 'device'
    terminals: list[Terminal] = Field(min_length=1, max_length=64)
    x: float | None = Field(default=None, ge=50, le=1500, description='QET 绘图坐标；省略自动布置')
    y: float | None = Field(default=None, ge=60, le=900)
    source: str = Field(default='', max_length=2000, description='覆盖共同来源；禁止按位号猜测端子或 PLC 通道')

    @model_validator(mode='after')
    def unique_terminals(self):
        if len({p.id for p in self.terminals}) != len(self.terminals):
            raise ValueError('元件内端子编号重复：'+self.id)
        if (self.x is None) != (self.y is None):
            raise ValueError('x/y 必须同时提供或同时省略')
        if self.kind in {'contact_no', 'contact_nc', 'relay'} and len(self.terminals) != 2:
            raise ValueError('接点或继电器线圈需要两个明确端子：'+self.id)
        return self


class Endpoint(Inputs):
    component: str = Field(min_length=1, max_length=80)
    terminal: str = Field(min_length=1, max_length=40)


class Wire(Inputs):
    id: str = Field(min_length=1, max_length=80)
    start: Endpoint
    end: Endpoint
    cable: str = Field(default='', max_length=80)
    core: str = Field(default='', max_length=40)
    section_mm2: float | None = Field(default=None, gt=0, le=1000)
    source: str = Field(default='', max_length=2000)


class Folio(Inputs):
    title: str = Field(min_length=1, max_length=160)
    components: list[Component] = Field(min_length=1, max_length=12)
    wires: list[Wire] = Field(min_length=1, max_length=200)


class QElectroTechInput(Inputs):
    action: Literal['status', 'check', 'create', 'inspect'] = 'create'
    title: str = Field(default='仪表与电气接线', min_length=1, max_length=160)
    source: str = Field(default='', max_length=4000, description='连接依据或明确的示例假设；create/check 必填')
    folios: list[Folio] = Field(default_factory=list, max_length=30)
    path: str | None = Field(default=None, max_length=2000, description='inspect 的 .qet 文件，受 MCP roots 限制')

    @model_validator(mode='after')
    def action_fields(self):
        if self.action in {'create', 'check'}:
            if not self.folios or not self.source.strip():
                raise ValueError('create/check 需要 folios 和明确的共同 source')
            if self.path:
                raise ValueError('create/check 不接受 path；原生文件用 inspect')
        elif self.folios or self.source:
            raise ValueError('status/inspect 不接受建模数据')
        if self.action == 'inspect' and not self.path:
            raise ValueError('inspect 需要 path')
        if self.action == 'status' and self.path:
            raise ValueError('status 不接受 path')
        return self
