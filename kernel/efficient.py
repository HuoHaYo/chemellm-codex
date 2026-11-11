"""Efficient workflow routes, sharing the existing kernel and artifact store."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time

from fastapi import HTTPException
from app import efficiency as e, project_store as store
from app.db import connection, now


def install(k):
    def checked(project_id):
        project = k.load_project(project_id)
        if project.get('workflow') != 'efficient':
            raise HTTPException(409, '旧项目须显式调用 chem_project_init(upgrade=true)')
        return project

    @k.app.post('/v1/project-init')
    def project_init(p: e.ProjectInit):
        # Validate before changing either storage backend.
        try:
            requirements = [k.RequirementItem.model_validate(r).model_dump() for r in p.requirements]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len({r['code'] for r in requirements}) != len(requirements) or len({d.key for d in p.deliverables}) != len(p.deliverables):
            raise HTTPException(422, '要求或交付项标识重复')
        ident = p.project_id or k.uid()
        with k.DWSIM_STUDIES.guard(ident):
            backup = None
            if p.project_id:
                project = k.load_project(ident)
                legacy = project.get('workflow') != 'efficient'
                if legacy and not p.upgrade:
                    raise HTTPException(409, '旧项目升级须显式设置 upgrade=true')
                if project.get('_store_revision', 0) != p.expected_revision:
                    raise HTTPException(409, '项目版本已变化，请重新读取')
                if legacy:
                    backup = k.project_dir(ident)/'legacy-backup.json'
                    if backup.exists():
                        raise HTTPException(409, '已存在迁移备份，先核对上次迁移状态')
                    shutil.copy2(k.project_file(ident), backup)
                    snapshot = k.SNAPSHOT_DIR/ident
                    if snapshot.exists():
                        shutil.copytree(snapshot, k.project_dir(ident)/'migration-snapshot-backup')
            else:
                if not p.name.strip() or p.expected_revision:
                    raise HTTPException(422, '新项目需要名称且 expected_revision=0')
                project = dict(id=ident, name=p.name, createdAt=now(), objects={}, files=[], calculations=[],
                               references=[], review={}, bundle=None, requirements=[], plan={'objects': [], 'deliverables': []})
            original = deepcopy(project)
            project.update(workflow='efficient', schema_version=2, task_kind=p.task_kind or project.get('task_kind','design'))
            if p.name:
                project['name'] = p.name
            reqs = {r['code']: r for r in project.get('requirements', [])}
            reqs.update({r['code']: r for r in requirements}); project['requirements'] = list(reqs.values())
            plan = project.setdefault('plan', {})
            ds = {d['key']: d for d in plan.get('deliverables', [])}
            ds.update({d.key: d.model_dump() for d in p.deliverables}); plan['deliverables'] = list(ds.values())
            project['review'] = {}; project['bundle'] = None
            # Never adopt historical failures or claim old files cover new items.
            k.ensure_legacy_project(project)
            try:
                k.save_project(project)
                if backup:
                    actual = store.load(ident)
                    for field in ('objects', 'calculations', 'files', 'references'):
                        if actual.get(field, [] if field != 'objects' else {}) != original.get(field, [] if field != 'objects' else {}):
                            raise ValueError('迁移一致性核对失败：'+field)
            except Exception:
                if backup:
                    store.delete(ident)
                    shutil.copy2(backup, k.project_file(ident))
                    snapshot = k.SNAPSHOT_DIR/ident
                    shutil.rmtree(snapshot, ignore_errors=True)
                    saved = k.project_dir(ident)/'migration-snapshot-backup'
                    if saved.exists():
                        shutil.copytree(saved, snapshot)
                raise
            return {'ok': True, 'project': e.summary(project), 'upgraded': bool(backup),
                    'backup': str(backup) if backup else None}

    @k.app.get('/v1/projects/{project_id}/records')
    def records(project_id: str, section: str = 'objects', offset: int = 0, limit: int = 20, key: str | None = None):
        k.project_dir(project_id)
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, 'offset/limit 无效')
        if section in {'deliverables','requirements','review'}:
            project = store.metadata(project_id) if store.exists(project_id) else k.load_project(project_id)
            value = (project.get('plan', {}).get('deliverables', []) if section=='deliverables' else
                     project.get('requirements', []) if section=='requirements' else [project.get('review',{})])
            if key:
                value = [row for row in value if row.get('key',row.get('code'))==key]
            return dict(ok=True,items=value[offset:offset+limit],total=len(value),
                        next_offset=offset+limit if offset+limit<len(value) else None)
        if section == 'runs':
            if not 0 <= offset or not 1 <= limit <= 100:
                raise HTTPException(422, 'offset/limit 无效')
            if not k.project_file(project_id).exists():
                raise HTTPException(404, '项目不存在')
            with connection() as db:
                where, args = 'project_id=?', [project_id]
                if key:
                    where += ' AND id=?';args.append(key)
                count = db.execute('SELECT count(*) FROM runs WHERE '+where,args).fetchone()[0]
                items = [dict(row) for row in db.execute('SELECT * FROM runs WHERE '+where+' ORDER BY created_at LIMIT ? OFFSET ?',
                                                       [*args, limit, offset])]
            for item in items:
                item['request'] = json.loads(item['request']);item['result'] = json.loads(item['result'])
            return dict(ok=True,items=items,total=count,next_offset=offset+len(items) if offset+len(items)<count else None)
        if not store.exists(project_id):
            project = k.load_project(project_id)
            if section == 'history':
                values = [h for obj in project.get('objects', {}).values() for h in obj.get('selectionHistory', [])]
            elif section in store.COLLECTIONS:
                value = project.get(section, {} if store.COLLECTIONS[section] == 'map' else [])
                values = [{'key': a, 'data': b} for a,b in value.items() if key is None or a == key] if isinstance(value, dict) else value
            else:
                raise HTTPException(422, '未知记录类型')
            if offset < 0 or not 1 <= limit <= 100:
                raise HTTPException(422, 'offset/limit 无效')
            return {'ok': True, 'items': values[offset:offset+limit], 'total': len(values),
                    'next_offset': offset+limit if offset+limit<len(values) else None}
        try:
            return {'ok': True, **store.records(project_id, section, offset, limit, key)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @k.app.post('/v1/projects/{project_id}/artifacts/register')
    @k.project_write
    def artifacts_register(project_id: str, p: e.ArtifactRegistration):
        started = time.perf_counter()
        project = checked(project_id)
        try:
            root = e.allowed_path(p.root, k.task_roots())
            if not root.is_dir():
                raise ValueError('登记根目录不存在')
            deliverables = {d['key'] for d in project.get('plan', {}).get('deliverables', [])}
            calculations = {c.get('calculationId') for c in project.get('calculations', [])}
            if any(f.calculation_id and f.calculation_id not in calculations for f in p.files):
                raise ValueError('成果引用的计算不存在')
            for keys in [f.deliverable_keys for f in p.files]+[c.deliverable_keys for c in p.checks]:
                if set(keys)-deliverables:
                    raise ValueError('引用了不存在的交付项')
            staged = Path(tempfile.mkdtemp(prefix='register-', dir=k.KERNEL_DATA))
            manifest = []
            try:
                hash_ms = 0
                for entry in p.files:
                    relative = e.relative_path(entry.path)
                    path = e.allowed_path(root/relative, [root])
                    if not path.is_file():
                        raise ValueError('登记文件不存在：'+relative)
                    dest = staged/relative; dest.parent.mkdir(parents=True, exist_ok=True)
                    stat = path.stat(); shutil.copyfile(path, dest)
                    hashing = time.perf_counter(); digest = e.file_digest(dest)
                    hash_ms += (time.perf_counter()-hashing)*1000
                    after = path.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or (entry.sha256 and digest != entry.sha256):
                        raise ValueError('文件复制期间变化或哈希不符：'+relative)
                    manifest.append(dict(relative_path=relative, sha256=digest, bytes=dest.stat().st_size,
                                         deliverable_keys=entry.deliverable_keys, calculation_id=entry.calculation_id))
                fingerprint = json.dumps({'project': project_id, 'generator': p.generator, 'run_id': p.run_id,
                                          'files': sorted(manifest, key=lambda f:f['relative_path']),
                                          'checks': sorted([c.model_dump() for c in p.checks], key=lambda c:(c['name'], c['report_path']))}, sort_keys=True)
                group = hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
                folder = k.KERNEL_DATA/'artifacts'/'registered'/group
                known = {f.get('artifactId') for f in project.get('files', [])}
                added = []
                with connection() as db:
                    for entry in manifest:
                        aid = hashlib.sha256((group+'/'+entry['relative_path']).encode()).hexdigest()[:32]
                        target = folder/entry['relative_path']; target.parent.mkdir(parents=True, exist_ok=True)
                        if target.exists() and e.file_digest(target) != entry['sha256']:
                            raise ValueError('已有固定版本被修改：'+entry['relative_path'])
                        if not target.exists():
                            shutil.copyfile(staged/entry['relative_path'], target)
                        metadata = {**entry, 'generator': p.generator, 'run_id': p.run_id, 'external': True}
                        db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)',
                            (aid, project_id, target.name, target.suffix.lstrip('.'), str(target), None, '', json.dumps(metadata), now()))
                        if aid not in known:
                            added.append(dict(artifactId=aid, name=target.name, kind=target.suffix.lstrip('.'),
                                origin='generated', registration_id=group, **entry,
                                downloadPath='/v1/artifacts/'+aid+'/download', generator=p.generator, run_id=p.run_id))
                    # State rows are committed separately; retry reconciles any orphan artifact rows.
                project.setdefault('files', []).extend(added)
                external = project.setdefault('external_checks', [])
                for c in p.checks:
                    report = next(x for x in manifest if x['relative_path'] == e.relative_path(c.report_path))
                    item = {**c.model_dump(), 'registration_id': group, 'report_sha256': report['sha256'],
                            'report_artifact_id': hashlib.sha256((group+'/'+report['relative_path']).encode()).hexdigest()[:32],
                            'source': 'external', 'independently_verified': False}
                    if item not in external:
                        external.append(item)
                if added:
                    project['review'] = {}; project['bundle'] = None
                    k.save_project(project)
                metrics = dict(registration_ms=round((time.perf_counter()-started)*1000, 3),
                               hash_ms=round(hash_ms, 3), files=len(manifest), added=len(added))
                k.record_metrics('registration', metrics)
                return dict(ok=True, registration_id=group, added=len(added), files=len(manifest),
                            summary=e.summary(project), metrics=metrics)
            finally:
                shutil.rmtree(staged)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return {'project_init': project_init, 'records': records, 'artifacts_register': artifacts_register}
