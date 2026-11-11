"""Select engineering inputs by provenance; never invent a missing number.

The calling agent researches, calculates and proposes candidates. This module
checks units, retains the evidence and applies the user's priority policy.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Literal

import pint
from pydantic import Field, field_validator, model_validator

from app.schemas import Inputs

units = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
SourceType = Literal['user', 'reference', 'derived', 'assumption']
POLICY = 'user > reference_or_derived > assumption'


class DerivationInput(Inputs):
    key: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=80)
    source_type: SourceType
    source: str = Field(min_length=1, max_length=4000)
    assumed: bool = False

    @model_validator(mode='after')
    def consistent(self):
        if any(not s.strip() for s in (self.key, self.unit, self.source)):
            raise ValueError('推算输入的标识、单位和来源不能是空白')
        convert(self.value, self.unit, self.unit)
        return self


class Derivation(Inputs):
    method: str = Field(min_length=1, max_length=4000)
    formula: str = Field(min_length=1, max_length=4000)
    inputs: list[DerivationInput] = Field(min_length=1, max_length=100)
    calculation_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode='after')
    def unique_inputs(self):
        if not self.method.strip() or not self.formula.strip():
            raise ValueError('推算方法和公式不能是空白')
        if len({i.key for i in self.inputs}) != len(self.inputs):
            raise ValueError('推算输入 key 必须唯一')
        return self


class NumericCandidate(Inputs):
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    value: float
    unit: str = Field(min_length=1, max_length=80)
    source_type: SourceType
    source: str = Field(min_length=1, max_length=4000)
    conditions: str = Field(min_length=1, max_length=3000)
    rationale: str = Field(min_length=1, max_length=4000)
    assumed: bool = Field(default=False, description='题设假定或推算中仍有假设时为 true；不改变用户数据优先级')
    applicable: bool = True
    applicability_reason: str = Field(min_length=1, max_length=3000)
    low: float | None = None
    high: float | None = None
    derivation: Derivation | None = None

    @field_validator('value', mode='before')
    @classmethod
    def reject_bool(cls, value):
        if isinstance(value, bool):
            raise ValueError('数值不能是布尔值')
        return value

    @model_validator(mode='after')
    def consistent(self):
        if any(not s.strip() for s in (self.unit, self.source, self.conditions, self.rationale, self.applicability_reason)):
            raise ValueError('单位、来源、条件、理由和适用性说明不能是空白')
        if self.source_type == 'derived' and self.derivation is None:
            raise ValueError('推算数值须保留方法、公式及带来源的实际输入')
        if self.source_type != 'derived' and self.derivation is not None:
            raise ValueError('含推算链的数值使用 source_type=derived')
        if (self.low is None) != (self.high is None):
            raise ValueError('不确定性上下限须同时填写')
        if self.low is not None and not self.low <= self.value <= self.high:
            raise ValueError('不确定性范围须包含候选值')
        return self


class QuantitySelection(Inputs):
    key: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=80, description='统一后的目标单位；压力基准等口径写入 conditions')
    conditions: str = Field(min_length=1, max_length=3000)
    candidates: list[NumericCandidate] = Field(min_length=1, max_length=100)
    preferred_candidate_id: str | None = Field(default=None, max_length=100, description='只可在最高可用优先级内择优；新用户数据替换旧用户数据时显式选取')
    exclusions: dict[str, str] = Field(default_factory=dict, description='不再适用候选的 id 和具体原因；原候选及理由仍留存')
    search_summary: str = Field(min_length=1, max_length=6000, description='实际查找的数据、渠道、条件和结果；用户已直接提供时记录无需补查；不得声称未执行的搜索')
    selection_reason: str = Field(min_length=1, max_length=4000, description='适用性、同级冲突处理、推算或假设的采用依据')
    minimum: float | None = Field(default=None, description='目标单位下的适用下界，须有依据')
    maximum: float | None = Field(default=None, description='目标单位下的适用上界，须有依据')
    role: Literal['process', 'solver', 'geometry'] = 'process'
    critical: bool = True
    source_group: str | None = None
    source_group_revision: int | None = None

    @model_validator(mode='after')
    def consistent(self):
        if len({c.id for c in self.candidates}) != len(self.candidates):
            raise ValueError('候选 id 不能重复')
        if any(not s.strip() for s in (self.key, self.unit, self.conditions, self.search_summary, self.selection_reason)):
            raise ValueError('数值标识、单位、条件、检索记录和选取理由不能是空白')
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError('适用下界不能大于上界')
        if any(not reason.strip() for reason in self.exclusions.values()):
            raise ValueError('排除候选必须给出具体原因')
        return self


class SourceGroup(Inputs):
    expected_revision: int = Field(default=0, ge=0)
    source_type: SourceType
    source: str = Field(min_length=1, max_length=4000)
    conditions: str = Field(min_length=1, max_length=3000)
    search_summary: str = Field(min_length=1, max_length=6000)
    rationale: str = Field(min_length=1, max_length=4000)
    applicability_reason: str = Field(min_length=1, max_length=3000)
    assumed: bool = False

    @model_validator(mode='after')
    def meaningful(self):
        if any(not getattr(self,k).strip() for k in ('source','conditions','search_summary','rationale','applicability_reason')):
            raise ValueError('来源组的来源、条件、检索说明和适用理由不能空白')
        return self


class CompactQuantity(Inputs):
    key: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=80)
    source_group: str = Field(min_length=1, max_length=80)
    candidate_id: str = Field(default='input-v1', pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    overrides: dict = Field(default_factory=dict, description='仅填写与来源组不同的候选条件/理由；derived须给derivation')
    role: Literal['process', 'solver', 'geometry'] = 'process'
    critical: bool = False
    preferred_candidate_id: str | None = None
    exclusions: dict[str, str] = Field(default_factory=dict)
    selection_reason: str | None = None

    @field_validator('value', mode='before')
    @classmethod
    def finite_value(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('数值必须是有限实数，不能是布尔值')
        return value


class BasisSelectionIn(Inputs):
    kind: str = Field(min_length=1, max_length=40)
    key: str = Field(min_length=1, max_length=80)
    title: str = Field(default='', max_length=300)
    expected_revision: int = Field(ge=0, description='新对象用 0，更新用当前对象版本')
    reason: str = Field(min_length=1, max_length=4000)
    quantities: list[QuantitySelection | CompactQuantity] = Field(default_factory=list, max_length=5000)
    source_groups: dict[str, SourceGroup] = Field(default_factory=dict)

    @model_validator(mode='after')
    def consistent(self):
        if not self.reason.strip() or not self.kind.strip() or not self.key.strip():
            raise ValueError('工程对象标识和更新理由不能是空白')
        if len({q.key for q in self.quantities}) != len(self.quantities):
            raise ValueError('同批数值 key 不能重复')
        if not self.quantities and not self.source_groups:
            raise ValueError('数值与来源组不能同时为空')
        return self


def expand_request(request: BasisSelectionIn, project: dict):
    """Resolve shorthand once, preserving the existing selection policy."""
    groups = deepcopy(project.get('source_groups', {}))
    for key, group in request.source_groups.items():
        if not key or len(key) > 80 or any(c in key for c in '/\\\x00'):
            raise ValueError('来源组标识无效')
        previous = groups.get(key, {})
        if group.expected_revision != previous.get('revision', 0):
            raise ValueError('来源组版本已变化：'+key)
        data = group.model_dump(exclude={'expected_revision'})
        groups[key] = dict(data, revision=group.expected_revision+1)
    expanded = []
    for q in request.quantities:
        if isinstance(q, QuantitySelection):
            expanded.append(q)
            continue
        group = groups.get(q.source_group)
        if not group:
            raise ValueError('来源组不存在：'+q.source_group)
        candidate = {k: v for k, v in group.items() if k not in {'revision', 'search_summary'}}
        if any(k in q.overrides for k in ('id', 'value', 'unit')):
            raise ValueError('数值与候选标识不能通过 overrides 覆盖')
        candidate.update(q.overrides)
        candidate.update(id=q.candidate_id, value=q.value, unit=q.unit)
        expanded.append(QuantitySelection(key=q.key, unit=q.unit, conditions=candidate['conditions'],
            candidates=[NumericCandidate.model_validate(candidate)], search_summary=group['search_summary'],
            selection_reason=q.selection_reason or candidate['rationale'], role=q.role, critical=q.critical,
            preferred_candidate_id=q.preferred_candidate_id, exclusions=q.exclusions,
            source_group=q.source_group, source_group_revision=group['revision']))
    return request.model_copy(update={'quantities': expanded, 'source_groups': {}}), groups


def is_assumed(candidate: NumericCandidate) -> bool:
    return (candidate.assumed or candidate.source_type == 'assumption' or
            bool(candidate.derivation and any(i.assumed or i.source_type == 'assumption' for i in candidate.derivation.inputs)))


def priority(candidate: NumericCandidate) -> int:
    if candidate.source_type == 'user':
        return 0
    return 2 if is_assumed(candidate) else 1


def convert(value: float, unit: str, target: str) -> float:
    try:
        result = float(units.Quantity(value, unit).to(target).magnitude)
    except (pint.PintError, ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f'单位不能换算：{unit} → {target}') from exc
    if not math.isfinite(result):
        raise ValueError('单位换算后的数值必须有限')
    return result


def select_quantity(request: QuantitySelection, previous: dict | None = None) -> dict:
    previous = previous or {}
    old_selection = previous.get('selection') or {}
    if previous.get('conditions') and previous['conditions'] != request.conditions:
        raise ValueError('工况或数值口径变化请使用新的数值 key 或情景对象，保留原设计依据')
    ledger = {c['id']: NumericCandidate.model_validate(c) for c in old_selection.get('candidates', [])}
    for candidate in request.candidates:
        if candidate.id in ledger and ledger[candidate.id] != candidate:
            raise ValueError(f'候选 {candidate.id} 已留存，修正数值或来源请使用新的候选 id')
        ledger[candidate.id] = candidate
    # Adopt a legacy quantity only when the caller explicitly supplies matching
    # provenance. This prevents an incomplete migration silently discarding it.
    if previous and not old_selection and previous.get('value') is not None:
        existing = ledger.get('existing')
        try:
            matches = existing is not None and math.isclose(convert(existing.value, existing.unit, previous['unit']),
                                                            float(previous['value']), rel_tol=1e-12, abs_tol=1e-12)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('旧数值的值或单位不完整，先补齐原记录或以新数值 key 保留暂定依据') from exc
        if not matches:
            raise ValueError('已有数值须用 id=existing 的候选保留原值并补齐来源后再选取')
        if previous.get('origin') in {'user', 'user_provided'} and existing.source_type != 'user':
            raise ValueError('已有用户数据须保留 source_type=user')
        if previous.get('origin') in {'reference', 'literature', 'datasheet'} and priority(existing) != 1:
            raise ValueError('已有资料数据须保留资料层级与来源')
        if (previous.get('origin') == 'assumption' or previous.get('assumed')) and not is_assumed(existing):
            raise ValueError('已有假设须保留假设标记')
    exclusions = {**old_selection.get('exclusions', {}), **request.exclusions}
    if set(exclusions) - ledger.keys():
        raise ValueError('排除列表含未知候选 id')
    decisions = []
    eligible = []
    for candidate in ledger.values():
        why = exclusions.get(candidate.id)
        if not why and not candidate.applicable:
            why = candidate.applicability_reason
        value = None if why else convert(candidate.value, candidate.unit, request.unit)
        if value is not None and ((request.minimum is not None and value < request.minimum) or
                                  (request.maximum is not None and value > request.maximum)):
            why = '不在本数值已声明的适用上下界内'
        if not why:
            eligible.append(candidate)
        decisions.append(dict(id=candidate.id, eligible=not bool(why), reason=why or candidate.applicability_reason,
                              priority=priority(candidate), assumed=is_assumed(candidate), normalized_value=value))
    if not eligible:
        raise ValueError('无适用候选：继续检索、推算或补入有依据的工程假设，保留该设计项')
    best_priority = min(priority(c) for c in eligible)
    best = [c for c in eligible if priority(c) == best_priority]
    preferred = request.preferred_candidate_id
    if preferred and preferred not in {c.id for c in best}:
        raise ValueError('指定候选不在最高可用优先级内，不能用低优先级数据覆盖用户数据或已有依据')
    old_id = old_selection.get('selected_id')
    chosen = next((c for c in best if c.id == (preferred or old_id)), best[0])
    assumed = is_assumed(chosen)
    result = dict(key=request.key, value=convert(chosen.value, chosen.unit, request.unit), unit=request.unit,
                  conditions=request.conditions, origin='assumption' if assumed else {'derived': 'calculation'}.get(chosen.source_type, chosen.source_type),
                  source_type=chosen.source_type, source=chosen.source, rationale=request.selection_reason,
                  assumed=assumed, sensitivity_required=assumed and request.role == 'process' and request.critical,
                  role=request.role, critical=request.critical,
                  selection=dict(version=1, policy=POLICY, selected_id=chosen.id, priority=priority(chosen),
                      candidates=[c.model_dump() for c in ledger.values()], exclusions=exclusions, decisions=decisions,
                      search_summary=request.search_summary, reason=request.selection_reason,
                      minimum=request.minimum, maximum=request.maximum,
                      evidence_verified=False))
    if request.source_group and chosen.id in {c.id for c in request.candidates}:
        result['source_group'] = request.source_group
        result['source_group_revision'] = request.source_group_revision
    elif previous.get('source_group') and chosen.id == old_id:
        result['source_group'] = previous['source_group']
        result['source_group_revision'] = previous.get('source_group_revision')
    if previous and not old_selection:
        result['selection']['legacy_quantity'] = deepcopy(previous)
    elif old_selection.get('legacy_quantity'):
        result['selection']['legacy_quantity'] = deepcopy(old_selection['legacy_quantity'])
    if chosen.low is not None:
        result['uncertainty'] = dict(low=convert(chosen.low, chosen.unit, request.unit), high=convert(chosen.high, chosen.unit, request.unit), unit=request.unit)
    if chosen.derivation:
        result['derivation'] = chosen.derivation.model_dump()
    return result


def apply_selection(previous: dict, request: BasisSelectionIn, timestamp: str) -> dict:
    """Return one complete, revisioned object; leave unrelated quantities intact."""
    result = deepcopy(previous)
    quantities = {q['key']: q for q in result.get('quantities', [])}
    changes = []
    for q in request.quantities:
        before = quantities.get(q.key)
        after = select_quantity(q, before)
        quantities[q.key] = after
        def brief(value):
            return {k: value.get(k) for k in ('value', 'unit', 'origin', 'source_type', 'assumed')} | {
                'candidate_id': value.get('selection', {}).get('selected_id')}
        changes.append(dict(key=q.key, before=brief(before) if before else None, after=brief(after),
                            exclusions=after['selection']['exclusions'], search_summary=q.search_summary, reason=q.selection_reason))
    revision = int(previous.get('revision') or 0) + 1
    result.update(kind=request.kind, key=request.key, title=request.title or previous.get('title') or request.key,
                  quantities=list(quantities.values()), revision=revision, updatedAt=timestamp)
    result.setdefault('attributes', {})
    result.setdefault('dependsOn', [])
    result.setdefault('selectionHistory', []).append(dict(revision=revision, createdAt=timestamp, reason=request.reason, changes=changes))
    return result


def selection_pending(objects: list[dict]) -> list[dict]:
    pending = []
    for obj in objects:
        for q in obj.get('quantities', []):
            if q.get('selection') and q.get('assumed'):
                pending.append(dict(object_key=obj['kind']+'/'+obj['key'], quantity_key=q['key'],
                    value=q['value'], unit=q['unit'], source=q['source'], sensitivity_required=q.get('sensitivity_required', True),
                    reason=('重要工艺假设需敏感性分析和后续数据标定' if q.get('sensitivity_required', True)
                            else '保留假设属性；按用途确认求解控制或展示几何，不重复生成敏感性任务')))
    return pending
