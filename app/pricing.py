"""Editable local price books with revision history and recoverable publication.

The AI supplies estimates and their rationale; this module never invents prices.
History is committed before publishing the editable current document. A small
journal repairs interrupted publication without discarding subsequent user edits.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.schemas import Inputs


class PriceConflict(ValueError):
    pass


class DesignCondition(Inputs):
    value: float
    unit: str = Field(min_length=1, max_length=40)


class Charge(Inputs):
    status: Literal['included', 'excluded', 'unknown'] = 'unknown'
    amount: float | None = Field(default=None, ge=0, description='与主价格同币种；口径由 note 说明，不能重复计入')
    rate: float | None = Field(default=None, ge=0, le=1, description='小数税率，例如 0.13；运费通常不填')
    note: str = Field(default='', max_length=2000)


class PriceItem(Inputs):
    key: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$', description='稳定价格项标识；更新沿用同一 key，规格和供应商变化保留在历史')
    category: Literal['equipment', 'installation', 'piping', 'instrument_electrical', 'civil',
                      'utility', 'engineering', 'contingency', 'raw_material', 'energy',
                      'catalyst', 'labor', 'maintenance', 'environmental', 'other']
    title: str = Field(min_length=1, max_length=300)
    equipment_tag: str = Field(default='', max_length=100)
    model_spec: str = Field(default='', max_length=3000)
    material: str = Field(default='', max_length=500)
    design_pressure: DesignCondition | None = None
    design_temperature: DesignCondition | None = None
    quantity: float = Field(gt=0)
    quantity_unit: str = Field(min_length=1, max_length=80, description='台、套、kg、kWh 等；必须与报价计价单位一致')
    unit_price: float = Field(ge=0, description='每 quantity_unit 的价格；整项报价用 quantity=1 并注明整项范围')
    currency: str = Field(pattern=r'^[A-Z]{3}$', description='三字母币种，例如 CNY、USD；不自动换汇')
    cost_basis: Literal['one_time', 'annual', 'per_batch', 'other']
    conditions: str = Field(min_length=1, max_length=3000, description='报价适用工况、地区、计价和期间口径')
    tax: Charge = Field(default_factory=Charge)
    freight: Charge = Field(default_factory=Charge)
    installation_scope: str = Field(default='', max_length=3000)
    source: str = Field(min_length=1, max_length=4000, description='每次新增或更新必填本次价格来源，如竞赛题目及页码、工厂/供应商报价、用户原话或估算依据；不得伪造来源')
    quote_date: date | None = None
    valid_until: date | None = None
    origin: Literal['quotation', 'assumption'] = Field(description='quotation=真实价格信息；assumption=假设价格，题设假定与 AI 暂估的区别写入 notes')
    rationale: str = Field(min_length=1, max_length=4000, description='估算方法、适用性或更新理由；假设必须解释为何采用此值')
    assumed_fields: list[str] = Field(default_factory=list, max_length=40, description='报价中自行补估的其他字段，如 quantity、tax.rate；应在 rationale 逐项说明')
    estimate_low: float | None = Field(default=None, ge=0)
    estimate_high: float | None = Field(default=None, ge=0)
    notes: str = Field(default='', max_length=4000, description='用户可编辑的价格备注，说明题设假定、某工厂报价或 AI 暂估，以及适用背景；引用时结合当前任务判断')

    @model_validator(mode='after')
    def consistent(self):
        if any(not value.strip() for value in (self.title, self.quantity_unit, self.conditions, self.source, self.rationale)):
            raise ValueError('名称、单位、条件、来源和理由不能是空白')
        if self.category == 'equipment' and not self.equipment_tag.strip():
            raise ValueError('设备价格必须绑定 equipment_tag 设备位号')
        if self.valid_until and self.quote_date and self.valid_until < self.quote_date:
            raise ValueError('有效期不能早于报价日期')
        if (self.estimate_low is None) != (self.estimate_high is None):
            raise ValueError('估值上下限须同时填写')
        if self.estimate_low is not None and not self.estimate_low <= self.unit_price <= self.estimate_high:
            raise ValueError('估值范围必须包含采用的单价')
        paths = {'quantity', 'quantity_unit', 'currency', 'cost_basis', 'model_spec', 'material',
                 'design_pressure', 'design_temperature', 'tax.amount', 'tax.rate', 'freight.amount',
                 'installation_scope', 'quote_date', 'valid_until', 'conditions'}
        if len(set(self.assumed_fields)) != len(self.assumed_fields) or set(self.assumed_fields) - paths:
            raise ValueError('assumed_fields 含重复或不支持的字段；价格本身是假设时使用 origin=assumption')
        if self.design_pressure and self.design_pressure.unit not in {'Pa', 'kPa', 'MPa', 'bar', 'barg', 'bara', 'MPa(g)', 'MPa(a)'}:
            raise ValueError('设计压力须使用压力单位，并在 conditions 说明表压/绝压')
        if self.design_temperature and self.design_temperature.unit not in {'K', 'degC', '°C', '℃', 'degF', '°F'}:
            raise ValueError('设计温度须使用温度单位')
        return self


class PriceDocument(Inputs):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, description='系统维护；手工编辑时保持原值')
    items: list[PriceItem] = Field(default_factory=list, max_length=5000)

    @model_validator(mode='after')
    def unique(self):
        if len({item.key for item in self.items}) != len(self.items):
            raise ValueError('同一价格表不能有重复 key')
        return self


class PriceWrite(Inputs):
    items: list[PriceItem] = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=3000)
    mode: Literal['upsert', 'fill_missing'] = 'upsert'

    @model_validator(mode='after')
    def unique(self):
        if len({item.key for item in self.items}) != len(self.items):
            raise ValueError('同一批不能重复更新价格 key')
        if self.mode == 'fill_missing' and any(item.origin != 'assumption' for item in self.items):
            raise ValueError('fill_missing 仅用于补充缺项假设；真实报价使用 upsert')
        if not self.reason.strip():
            raise ValueError('更新理由不能为空白')
        return self


def _text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def _atomic(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(_text(value))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class PriceBook:
    """Call under the kernel's project write lock (one writer process per project)."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.current = folder / 'prices.json'
        self.history = folder / 'history.json'
        self.pending = folder / '.pending.json'

    def _load_history(self):
        events = json.loads(self.history.read_text(encoding='utf-8-sig')) if self.history.exists() else []
        if not isinstance(events, list):
            raise ValueError('价格历史格式损坏；请保留文件并恢复备份')
        state = {}
        for revision, event in enumerate(events, 1):
            if event.get('revision') != revision:
                raise ValueError('价格历史版本不连续；请保留文件并恢复备份')
            for change in event['changes']:
                key = change['key']
                if state.get(key) != change['before']:
                    raise ValueError('价格历史前后版本不一致；请保留文件并恢复备份')
                if change['after'] is None:
                    state.pop(key, None)
                else:
                    item = PriceItem.model_validate(change['after']).model_dump(mode='json')
                    if item['key'] != key:
                        raise ValueError('价格历史 key 不一致')
                    state[key] = item
        return events, state

    def _recover(self):
        if not self.pending.exists():
            return
        pending = json.loads(self.pending.read_text(encoding='utf-8'))
        events, _ = self._load_history()
        event = pending['event']
        if event['revision'] == len(events) + 1:
            _atomic(self.history, events + [event])
        elif not events or event != events[-1]:
            raise ValueError('价格写入日志与历史不一致；请保留文件并恢复备份')
        events, state = self._load_history()
        # Preserve a manual edit made after the interrupted write. read() will
        # import it as the next revision instead of overwriting its contents.
        actual_hash = _file_hash(self.current)
        if actual_hash in (None, pending['before_hash']):
            _atomic(self.current, {'schema_version': 1, 'revision': len(events), 'items': list(state.values())})
        elif actual_hash != pending['after_hash']:
            document = PriceDocument.model_validate_json(self.current.read_text(encoding='utf-8-sig'))
            # The editor may still hold the document from before publication.
            if document.revision == len(events) - 1:
                document.revision = len(events)
                _atomic(self.current, document.model_dump(mode='json'))
        self.pending.unlink()

    def _commit(self, events, before, after, actor, reason, before_hash):
        changes = [{'key': key, 'before': before.get(key), 'after': after.get(key)}
                   for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)]
        if not changes:
            return
        if _file_hash(self.current) != before_hash:
            raise PriceConflict('价格文件在读取后又被编辑，请重新读取并合并更新')
        event = {'revision': len(events) + 1, 'recorded_at': datetime.now(timezone.utc).isoformat(),
                 'actor': actor, 'reason': reason, 'changes': changes}
        document = {'schema_version': 1, 'revision': event['revision'], 'items': list(after.values())}
        _atomic(self.pending, {'event': event, 'before_hash': before_hash,
                               'after_hash': hashlib.sha256(_text(document).encode('utf-8')).hexdigest()})
        self._recover()

    def read(self):
        for _ in range(4):
            self._recover()
            events, state = self._load_history()
            if not self.current.exists():
                _atomic(self.current, {'schema_version': 1, 'revision': len(events), 'items': list(state.values())})
                continue
            raw = self.current.read_bytes()
            observed_hash = hashlib.sha256(raw).hexdigest()
            document = PriceDocument.model_validate_json(raw.decode('utf-8-sig'))
            if document.revision != len(events):
                raise PriceConflict(f'价格表版本冲突：文件为 {document.revision}，历史为 {len(events)}；请合并当前版本后再编辑')
            incoming = {item.key: item.model_dump(mode='json') for item in document.items}
            if incoming != state:
                self._commit(events, state, incoming, 'local_edit', '用户直接编辑本地 prices.json（含增删改）', observed_hash)
                continue
            if not self.history.exists():
                _atomic(self.history, [])
            self._observed_hash = observed_hash
            return self._view(len(events), state)
        raise PriceConflict('价格文件正在连续编辑，请保存完成后重新读取')

    def save(self, request: PriceWrite):
        current = self.read()
        if current['revision'] != request.expected_revision:
            raise PriceConflict(f"价格版本冲突：当前为 {current['revision']}，请先读取再合并更新")
        before = {item['key']: item for item in current['items']}
        after = dict(before)
        for item in request.items:
            old = before.get(item.key)
            if request.mode == 'fill_missing' and old:
                continue
            if old and old['origin'] == 'quotation' and item.origin == 'assumption':
                raise ValueError('不能用假设覆盖已有真实价格：' + item.key)
            after[item.key] = item.model_dump(mode='json')
        if len(after) > 5000:
            raise ValueError('价格项超过 5000 条，请拆分项目')
        events, _ = self._load_history()
        self._commit(events, before, after, 'ai_tool', request.reason, self._observed_hash)
        return self.read()

    def events(self, key=None, offset=0, limit=50):
        view = self.read()
        events, _ = self._load_history()
        if key is not None:
            events = [{**event, 'changes': [change for change in event['changes'] if change['key'] == key]}
                      for event in events if any(change['key'] == key for change in event['changes'])]
        return {'revision': view['revision'], 'total': len(events), 'offset': offset, 'events': events[offset:offset + limit]}

    def _view(self, revision, state):
        pending = []
        today = date.today()
        for item in state.values():
            missing = [field for field in ('quote_date', 'valid_until', 'installation_scope') if not item[field]]
            if item['category'] == 'equipment':
                missing += [field for field in ('model_spec', 'material', 'design_pressure', 'design_temperature') if not item[field]]
            missing += [field for field in ('tax', 'freight') if item[field]['status'] == 'unknown']
            if item['tax']['status'] == 'excluded' and item['tax']['amount'] is None and item['tax']['rate'] is None:
                missing.append('tax.amount/rate')
            if item['freight']['status'] == 'excluded' and item['freight']['amount'] is None:
                missing.append('freight.amount')
            reasons = []
            if item['origin'] == 'assumption':
                reasons.append('价格为假设，来源与适用背景见 source、notes、rationale')
            if item['assumed_fields']:
                reasons.append('存在假设字段：' + '、'.join(item['assumed_fields']))
            if missing:
                reasons.append('资料待补充：' + '、'.join(missing))
            if item['valid_until'] and date.fromisoformat(item['valid_until']) < today:
                reasons.append('报价已超过有效期，待确认')
            if reasons:
                pending.append({'key': item['key'], 'title': item['title'], 'reasons': reasons, 'missing_fields': missing})
        return {'revision': revision, 'items': list(state.values()), 'pending_review': pending,
                'files': {'current': str(self.current.resolve()), 'history': str(self.history.resolve())}}
