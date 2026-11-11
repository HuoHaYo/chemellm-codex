"""Small public contracts and automatic record handling for efficient projects."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from fastapi import HTTPException

TaskKind = Literal['research', 'scene', 'simulation', 'design']
DEFAULT_CHECKS = {'research': [], 'scene': ['model', 'connections', 'spatial', 'consistency'],
                  'simulation': ['numeric'], 'design': ['numeric', 'basis']}


class Deliverable(BaseModel):
    key: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
    title: str = Field(min_length=1, max_length=200)
    requirementCodes: list[str] = Field(default_factory=list, max_length=100)
    formats: list[str] = Field(default_factory=list, max_length=20)
    task_kind: TaskKind | None = None
    checks: list[str] | None = None
    calculation_ids: list[str] = Field(default_factory=list, max_length=100)
    artifact_ids: list[str] = Field(default_factory=list, max_length=10000)
    basis_keys: list[str] = Field(default_factory=list, max_length=100)


class ProjectInit(BaseModel):
    project_id: str | None = None
    name: str = Field(default='', max_length=120)
    task_kind: TaskKind | None = None
    requirements: list[dict] = Field(default_factory=list, max_length=200)
    deliverables: list[Deliverable] = Field(default_factory=list, max_length=200)
    expected_revision: int = Field(default=0, ge=0)
    upgrade: bool = False


class RegisteredFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000, description='root 下相对路径，保留目录结构')
    deliverable_keys: list[str] = Field(default_factory=list, max_length=100)
    sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    calculation_id: str | None = None


class ExternalCheck(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    passed: bool
    report_path: str = Field(min_length=1, max_length=1000)
    deliverable_keys: list[str] = Field(min_length=1, max_length=100)


class ArtifactRegistration(BaseModel):
    root: str = Field(min_length=1, max_length=4000)
    generator: str = Field(min_length=1, max_length=1000)
    run_id: str = Field(min_length=1, max_length=200)
    files: list[RegisteredFile] = Field(min_length=1, max_length=10000)
    checks: list[ExternalCheck] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def unique(self):
        paths = [relative_path(f.path) for f in self.files]
        if len({s.casefold() for s in paths}) != len(paths):
            raise ValueError('成果相对路径重复')
        if any(relative_path(c.report_path) not in paths for c in self.checks):
            raise ValueError('检查报告须与成果一同登记')
        return self


def relative_path(raw):
    value = raw.replace('\\', '/')
    parts = PurePosixPath(value)
    if parts.is_absolute() or '..' in parts.parts or not parts.parts or any(':' in p for p in parts.parts):
        raise ValueError('成果必须使用不含 .. 或盘符的相对路径')
    if any(p in {'.git', '.aws', '.codex', '.agents'} for p in parts.parts):
        raise ValueError('不能登记配置或版本库目录')
    return parts.as_posix()


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def allowed_path(raw, roots):
    path = Path(raw).expanduser().resolve()
    if not any(path.is_relative_to(Path(r).resolve()) for r in roots):
        raise ValueError('路径不在 MCP roots 或 --allowed-root 允许目录内')
    return path


def summary(project):
    review = project.get('review') or {}
    return dict(projectId=project['id'], name=project['name'], workflow=project.get('workflow', 'legacy'),
                task_kind=project.get('task_kind', 'design'), phase=project.get('phase'),
                revision=project.get('_store_revision', project.get('revision', 0)),
                counts=project.get('counts') or {s: len(project.get(s, [])) for s in
                    ('objects', 'source_groups', 'calculations', 'files', 'requirements')},
                ready=review.get('ready', False), gap_count=len(review.get('blockers', [])),
                gaps=[str(s)[:240] for s in review.get('blockers', [])[:10]], updatedAt=project.get('updatedAt'))


def check_calculation(project, ident):
    record = next((c for c in project.get('calculations', []) if c.get('calculationId') == ident), None)
    if not record:
        return '计算不存在：'+ident
    validation = record.get('validation') or {}
    if validation.get('passed') is not True:
        return '当前采用的计算未通过独立校核：'+ident
    if record.get('tool') == 'dwsim' and record.get('solved') is not True:
        return '当前采用的 DWSIM 未求解：'+ident
    if record.get('priceRevision') is not None and record['priceRevision'] != project.get('prices', {}).get('revision'):
        return '计算采用的价格已过期：'+ident
    for key, revision in record.get('basis_revisions', {}).items():
        if project.get('objects', {}).get(key, {}).get('revision') != revision:
            return '计算采用的依据已过期：'+key
    for key, revision in record.get('source_group_revisions', {}).items():
        if project.get('source_groups', {}).get(key, {}).get('revision') != revision:
            return '计算采用的来源组已过期：'+key
    return None


def selected_files(project, deliverable):
    attached = [f for f in project.get('files', []) if deliverable['key'] in f.get('deliverable_keys', [])]
    if deliverable.get('artifact_ids'):
        return [f for f in attached if f['artifactId'] in deliverable['artifact_ids']]
    if deliverable.get('calculation_ids'):
        return [f for f in attached if not f.get('calculation_id') or f['calculation_id'] in deliverable['calculation_ids']]
    return attached


def delivery_files(project):
    ids = {f['artifactId'] for d in project.get('plan', {}).get('deliverables', []) for f in selected_files(project, d)}
    groups = {f['registration_id'] for f in project.get('files', []) if f.get('artifactId') in ids and f.get('registration_id')}
    return [f for f in project.get('files', []) if f.get('artifactId') in ids or
            f.get('registration_id') in groups or f.get('origin') == 'reference']


def review(project, artifact_path, note=''):
    blockers, warnings = [], []
    deliverables = project.get('plan', {}).get('deliverables', [])
    if not project.get('requirements'):
        blockers.append('缺少用户要求')
    if not deliverables:
        blockers.append('缺少交付计划')
    required = {r['code'] for r in project.get('requirements', []) if r.get('mandatory', True)}
    mapped = {c for d in deliverables for c in d.get('requirementCodes', [])}
    if required-mapped:
        blockers.append('要求未映射：'+'、'.join(sorted(required-mapped)))
    for d in deliverables:
        attached = selected_files(project, d)
        if d.get('artifact_ids'):
            for ident in d['artifact_ids']:
                if not any(f['artifactId'] == ident for f in attached):
                    blockers.append('采用文件不存在或未关联交付项：'+ident)
        if not attached:
            blockers.append('交付项缺少明确文件映射：'+d['key'])
        for fmt in d.get('formats', []):
            if not any(f.get('kind', '').lower() == fmt.lower().lstrip('.') for f in attached):
                blockers.append('交付项缺少格式：'+d['key']+'/'+fmt)
        checks = d.get('checks')
        checks = DEFAULT_CHECKS[d.get('task_kind') or project.get('task_kind', 'design')] if checks is None else checks
        if 'numeric' in checks:
            if not d.get('calculation_ids'):
                blockers.append('未指定当前采用计算：'+d['key'])
            for ident in d.get('calculation_ids', []):
                issue = check_calculation(project, ident)
                if issue:
                    blockers.append(issue)
        if 'basis' in checks:
            if not d.get('basis_keys'):
                blockers.append('缺少当前设计依据映射：'+d['key'])
            for key in d.get('basis_keys', []):
                obj = project.get('objects', {}).get(key)
                if not obj or not obj.get('quantities'):
                    blockers.append('设计依据缺失：'+key)
                elif any(not q.get('unit') or not q.get('source') or q.get('origin') == 'unknown' for q in obj['quantities']):
                    blockers.append('设计依据单位或来源待确认：'+key)
                elif any(q.get('source_group') and q.get('source_group_revision') !=
                         project.get('source_groups', {}).get(q['source_group'], {}).get('revision') for q in obj['quantities']):
                    blockers.append('设计依据引用来源组已过期：'+key)
            adopted = [c for c in project.get('calculations', []) if c.get('calculationId') in d.get('calculation_ids', [])]
            for record in adopted:
                if set(d.get('basis_keys', []))-record.get('basis_revisions', {}).keys():
                    blockers.append('采用计算未固定必要设计依据版本：'+str(record.get('calculationId')))
        for name in checks:
            if name in {'numeric', 'basis'}:
                continue
            groups = {f['registration_id'] for f in attached if f.get('registration_id')}
            reports = [c for c in project.get('external_checks', []) if c['name'] == name and d['key'] in c['deliverable_keys']
                       and (not groups or c['registration_id'] in groups)]
            if not reports or any(not c['passed'] for c in reports) or groups-{c['registration_id'] for c in reports}:
                blockers.append('检查未通过或缺失：'+d['key']+'/'+name)
            for report in reports:
                warnings.append('外部报告声明 passed='+str(report['passed'])+'，非插件独立验证：'+d['key']+'/'+name)
                try:
                    if file_digest(artifact_path(report['report_artifact_id'])) != report['report_sha256']:
                        blockers.append('检查报告内容已改变：'+d['key']+'/'+name)
                except (OSError, ValueError, HTTPException):
                    blockers.append('检查报告缺失：'+d['key']+'/'+name)
        for f in attached:
            try:
                path = artifact_path(f['artifactId'])
                if f.get('sha256') and file_digest(path) != f['sha256']:
                    blockers.append('登记成果内容已改变：'+f['name'])
            except (OSError, ValueError, HTTPException):
                blockers.append('登记成果缺失：'+f['name'])
    return dict(ready=not blockers, blockers=list(dict.fromkeys(blockers)), warnings=list(dict.fromkeys(warnings)),
                professionalReview='not_reviewed', note=note[:2000], external_reports_independently_verified=False)


def capture_inputs(args, tool=None):
    """Capture explicit typed fields only. Do not infer units from arbitrary keys."""
    model = args.get('model') or {}
    if not isinstance(model, dict):
        model = {}
    quantities = []
    unit_fields = {'temperature_k': 'K', 'pressure_pa': 'Pa', 'mass_flow_kg_s': 'kg/s',
                   'pressure_drop_pa': 'Pa', 'efficiency': 'dimensionless', 'pressure_change_pa':'Pa',
                   'heat_duty_kw':'kW', 'area_m2':'m^2', 'overall_coefficient_w_m2_k':'W/(m^2*K)',
                   'hot_pressure_drop_pa':'Pa','cold_pressure_drop_pa':'Pa', 'reflux_ratio':'dimensionless',
                   'temperature_tolerance_k':'K','pressure_tolerance_pa':'Pa','mass_flow_tolerance_kg_s':'kg/s',
                   'composition_tolerance':'dimensionless','maximum_iterations':'dimensionless',
                   'top_pressure_pa':'Pa','bottom_pressure_pa':'Pa','stages':'dimensionless',
                   'stage_efficiency':'dimensionless','solver_tolerance':'dimensionless','external_tolerance':'dimensionless'}
    for obj in (model.get('objects', []) + args.get('updates', [])) if tool == 'dwsim' else []:
        spec = obj.get('spec') or {}
        for prefix, values in [('',obj), ('spec.',spec), ('spec.column.',spec.get('column') or {})]:
            for field, unit in unit_fields.items():
                if values.get(field) is not None:
                    control = 'tolerance' in field or field == 'maximum_iterations'
                    quantities.append(dict(key=str(obj.get('tag', '?'))+'.'+prefix+field, value=values[field], unit=unit,
                        origin='unknown', source='实际工具输入；来源待确认', role='solver' if control else 'process'))
        for component, value in (obj.get('mole_fractions') or {}).items():
            quantities.append(dict(key=obj['tag']+'.mole_fractions.'+component,value=value,unit='dimensionless',
                origin='unknown',source='实际摩尔分数输入；来源待确认',role='process'))
        for section in ('parameters', 'properties'):
            for key, value in obj.get(section, {}).items():
                quantities.append(dict(key=str(obj.get('tag', '?'))+'.'+section+'.'+key, value=value,
                                       unit=None, origin='unknown', source='原生参数；单位及来源待确认', role='process'))
    # Blender dimensions are explicitly defined by its schema, not inferred
    # from arbitrary property names. They remain presentation geometry.
    for item in args.get('items', []) if tool == 'export_blender' else []:
        if item.get('kind') not in {'box','column','vessel','tank','horizontal_vessel','exchanger','pump',
                                    'building','pipe_rack','pipe','road','fence','ground','cad'}:
            continue
        for field in ('length','width','height','diameter','position','points'):
            if item.get(field) is not None:
                quantities.append(dict(key=item['id']+'.'+field,value=item[field],unit=args.get('units','mm'),
                    origin='unknown',source=item.get('source') or '展示几何；来源待确认',role='geometry'))
    if tool not in {'dwsim','export_blender'}:
        def numeric(value, path=''):
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                quantities.append(dict(key=path,value=value,unit=args.get('from_unit') if tool=='convert_units' and path=='value' else None,
                    origin='unknown',source='实际工具输入；单位或来源待确认',role='process'))
            elif isinstance(value,dict):
                for key,item in value.items():numeric(item,path+'.'+key if path else key)
            elif isinstance(value,list):
                for index,item in enumerate(value):numeric(item,path+'.'+str(index))
        numeric(args)
    return quantities


def validation_summary(verification):
    validation = verification.get('validation') or {}
    return {'model': str(verification.get('model', ''))[:200],
            'validation': {k: validation[k] for k in ('passed', 'status') if k in validation},
            'details': 'chem_records(section=calculations) / chem_calculations'}


def review_summary(result):
    return {**result, 'blockers': [s[:240] for s in result['blockers'][:10]],
            'warnings': [s[:240] for s in result['warnings'][:5]],
            'blocker_count':len(result['blockers']), 'warning_count':len(result['warnings']),
            'details':'chem_project(view=full).review'}
