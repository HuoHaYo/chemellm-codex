"""Validated inputs for static, reproducible Blender presentation scenes."""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from app.schemas import Inputs

Vector = tuple[float, float, float]


class SceneReference(Inputs):
    key: str = Field(min_length=1, description='项目对象完整 key，例如 equipment/T-101')
    revision: int = Field(ge=1)


class SceneItem(Inputs):
    id: str = Field(min_length=1, max_length=120)
    kind: Literal['column', 'vessel', 'horizontal_vessel', 'tank', 'exchanger', 'pump',
                  'box', 'pipe', 'pipe_rack', 'road', 'building', 'fence', 'ground', 'cad']
    label: str = Field(default='', max_length=200)
    position: Vector = (0, 0, 0)
    rotation_deg: Vector = (0, 0, 0)
    length: float | None = Field(default=None, gt=0)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    diameter: float | None = Field(default=None, gt=0)
    points: list[Vector] = Field(default_factory=list, max_length=1000,
                                 description='管道/道路/围墙的局部路径；再应用 position 和 rotation_deg')
    bays: int = Field(default=4, ge=1, le=100, description='管廊沿 length 的跨数')
    color: tuple[float, float, float] | None = None
    source: str = Field(min_length=1, max_length=2000, description='尺寸与位置的来源或演示假设说明')
    reference: SceneReference | None = None
    path: str | None = Field(default=None, max_length=2000, description='CAD 文件在工作区/数据目录内的路径')
    object_names: list[str] = Field(default_factory=list, max_length=200,
                                    description='FCStd 可选导入对象 Name；空时选择可见的顶层几何')

    @model_validator(mode='after')
    def geometry(self):
        if self.color is not None and any(c < 0 or c > 1 for c in self.color):
            raise ValueError('color 分量必须在 0~1')
        required = {
            'column': ('diameter', 'height'), 'vessel': ('diameter', 'height'),
            'tank': ('diameter', 'height'), 'horizontal_vessel': ('diameter', 'length'),
            'exchanger': ('diameter', 'length'), 'pump': ('length', 'width', 'height'),
            'box': ('length', 'width', 'height'), 'building': ('length', 'width', 'height'),
            'ground': ('length', 'width'), 'pipe_rack': ('length', 'width', 'height'),
            'pipe': ('diameter',), 'road': ('width',), 'fence': ('height',),
        }
        for field in required.get(self.kind, ()):
            if getattr(self, field) is None:
                raise ValueError(f'{self.id}: {self.kind} 缺少 {field}')
        if self.kind in {'pipe', 'road', 'fence'}:
            if len(self.points) < 2 or any(a == b for a, b in zip(self.points, self.points[1:])):
                raise ValueError(f'{self.id}: 路径至少含两个点，且相邻点不能重合')
        elif self.points:
            raise ValueError('points 仅用于 pipe/road/fence')
        if self.kind == 'cad':
            if not self.path:
                raise ValueError('cad 必须指定 path')
        elif self.path or self.object_names:
            raise ValueError('path/object_names 仅用于 cad')
        return self


class SceneCamera(Inputs):
    position: Vector
    target: Vector
    lens_mm: float = Field(default=45, ge=10, le=200)

    @model_validator(mode='after')
    def distinct(self):
        if self.position == self.target:
            raise ValueError('相机位置不能与目标重合')
        return self


class SceneRender(Inputs):
    width: int = Field(default=1280, ge=64, le=3840)
    height: int = Field(default=720, ge=64, le=2160)
    camera: SceneCamera | None = None
    labels: bool = True
    font_path: str | None = Field(default=None, max_length=2000)


class BlenderInput(Inputs):
    title: str = Field(default='化工厂三维演示', max_length=200)
    units: Literal['mm', 'm'] = 'mm'
    items: list[SceneItem] = Field(min_length=1, max_length=2000)
    render: SceneRender = Field(default_factory=SceneRender)
    tessellation_mm: float = Field(default=2, gt=0, le=100,
                                   description='CAD 外观网格弦偏差，单位始终为 mm')

    @model_validator(mode='after')
    def unique(self):
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError('场景对象 id 不能重复')
        return self
