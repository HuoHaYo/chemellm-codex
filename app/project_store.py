"""Versioned v2 project state. SQLite is authoritative; JSON is an export.

Long strings are interned across records and revisions, so shared provenance is
stored once. Existing calculation/artifact tables remain the source of originals.
"""
from __future__ import annotations

import hashlib
import json
import time

from app.db import connection

COLLECTIONS = {'objects': 'map', 'source_groups': 'map', 'calculations': 'list',
               'files': 'list', 'references': 'list'}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


class StoreConflict(ValueError):
    pass


def init():
    with connection() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS codex_project_state(
          project_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, meta TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS codex_project_records(
          project_id TEXT NOT NULL, section TEXT NOT NULL, key TEXT NOT NULL,
          position INTEGER NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(project_id,section,key));
        CREATE TABLE IF NOT EXISTS codex_project_history(
          project_id TEXT NOT NULL, section TEXT NOT NULL, key TEXT NOT NULL,
          revision INTEGER NOT NULL, data TEXT, PRIMARY KEY(project_id,section,key,revision));
        CREATE TABLE IF NOT EXISTS codex_project_texts(
          digest TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')


def exists(project_id):
    with connection() as db:
        return db.execute('SELECT 1 FROM codex_project_state WHERE project_id=?', (project_id,)).fetchone() is not None


def _pack(value, db, texts):
    if isinstance(value, str) and len(value.encode('utf-8')) >= 96:
        digest = hashlib.sha256(value.encode()).hexdigest()
        if digest not in texts:
            db.execute('INSERT OR IGNORE INTO codex_project_texts VALUES(?,?)', (digest, value))
            texts[digest] = value
        return {'$text': digest}
    if isinstance(value, dict):
        # Envelope all dicts, including user data containing a literal "$text".
        return {'$dict': {k: _pack(v, db, texts) for k, v in value.items()}}
    if isinstance(value, list):
        return [_pack(v, db, texts) for v in value]
    return value


def _unpack(value, db, cache):
    if isinstance(value, dict):
        if '$text' in value:
            digest = value['$text']
            if digest not in cache:
                row = db.execute('SELECT value FROM codex_project_texts WHERE digest=?', (digest,)).fetchone()
                if row is None:
                    raise ValueError('Missing project text: '+digest)
                cache[digest] = row[0]
            return cache[digest]
        return {k: _unpack(v, db, cache) for k, v in value['$dict'].items()}
    if isinstance(value, list):
        return [_unpack(v, db, cache) for v in value]
    return value


def _identity(section, row, index):
    for key in {'files': ('artifactId',), 'calculations': ('calculationId',),
                'references': ('use_id',)}.get(section, ()):
        if row.get(key):
            return str(row[key])
    return 'index:'+str(index)


def save(project):
    started = time.perf_counter()
    meta = {k: v for k, v in project.items() if k not in COLLECTIONS and not k.startswith('_store_')}
    records = {}
    for section, kind in COLLECTIONS.items():
        value = project.get(section, {} if kind == 'map' else [])
        entries = value.items() if kind == 'map' else ((_identity(section, r, i), r) for i, r in enumerate(value))
        for i, (key, row) in enumerate(entries):
            if (section, key) in records:
                raise ValueError('Duplicate project record: '+section+'/'+key)
            records[section, key] = (i, row)
    changes, texts = [], {}
    serialization_ms = 0
    with connection() as db:
        acquiring = time.perf_counter()
        db.execute('BEGIN IMMEDIATE')
        db_lock_wait_ms = (time.perf_counter()-acquiring)*1000
        old = db.execute('SELECT revision FROM codex_project_state WHERE project_id=?', (project['id'],)).fetchone()
        revision = old[0] if old else 0
        if revision != project.get('_store_revision', 0):
            raise StoreConflict('项目版本已变化，请重新读取后合并')
        revision += 1
        previous = {(r['section'], r['key']): r for r in db.execute(
            'SELECT * FROM codex_project_records WHERE project_id=?', (project['id'],))}
        for (section, key), (position, value) in records.items():
            encoding = time.perf_counter()
            packed = _pack(value, db, texts)
            data = dumps(packed)
            serialization_ms += (time.perf_counter()-encoding)*1000
            before = previous.pop((section, key), None)
            if before and before['data'] == data and before['position'] == position:
                continue
            db.execute('INSERT OR REPLACE INTO codex_project_records VALUES(?,?,?,?,?,?)',
                       (project['id'], section, key, position, revision, data))
            db.execute('INSERT INTO codex_project_history VALUES(?,?,?,?,?)',
                       (project['id'], section, key, revision, data))
            changes.append((section, key, packed))
        for section, key in previous:
            db.execute('DELETE FROM codex_project_records WHERE project_id=? AND section=? AND key=?',
                       (project['id'], section, key))
            db.execute('INSERT INTO codex_project_history VALUES(?,?,?,?,NULL)', (project['id'], section, key, revision))
            changes.append((section, key, None))
        db.execute('INSERT OR REPLACE INTO codex_project_state VALUES(?,?,?)',
                   (project['id'], revision, dumps(_pack(meta, db, texts))))
    project['_store_revision'] = revision
    return changes, texts, {'storage_ms': round((time.perf_counter()-started)*1000, 3),
                            'serialization_ms': round(serialization_ms, 3), 'db_lock_wait_ms':round(db_lock_wait_ms,3),
                            'changed_records': len(changes)}


def load(project_id):
    with connection() as db:
        state = db.execute('SELECT * FROM codex_project_state WHERE project_id=?', (project_id,)).fetchone()
        if state is None:
            raise KeyError(project_id)
        cache = {}
        result = _unpack(json.loads(state['meta']), db, cache)
        result.update({s: {} if k == 'map' else [] for s, k in COLLECTIONS.items()})
        for row in db.execute('SELECT * FROM codex_project_records WHERE project_id=? ORDER BY section,position', (project_id,)):
            value = _unpack(json.loads(row['data']), db, cache)
            if COLLECTIONS[row['section']] == 'map':
                result[row['section']][row['key']] = value
            else:
                result[row['section']].append(value)
        result['_store_revision'] = state['revision']
        return result


def metadata(project_id):
    with connection() as db:
        row = db.execute('SELECT * FROM codex_project_state WHERE project_id=?', (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        result = _unpack(json.loads(row['meta']), db, {})
        result['revision'] = row['revision']
        result['counts'] = {r[0]: r[1] for r in db.execute(
            'SELECT section,count(*) FROM codex_project_records WHERE project_id=? GROUP BY section', (project_id,))}
        for section in COLLECTIONS:
            result['counts'].setdefault(section,0)
        result['counts']['requirements'] = len(result.get('requirements',[]))
        result['counts']['deliverables'] = len(result.get('plan',{}).get('deliverables',[]))
        return result


def records(project_id, section, offset=0, limit=20, key=None):
    if section not in {*COLLECTIONS, 'history'} or offset < 0 or not 1 <= limit <= 100:
        raise ValueError('records section/offset/limit 无效')
    table = 'codex_project_history' if section == 'history' else 'codex_project_records'
    where, args = 'project_id=?', [project_id]
    if section != 'history':
        where += ' AND section=?'; args.append(section)
    if key is not None:
        where += ' AND key=?'; args.append(key)
    with connection() as db:
        count = db.execute(f'SELECT count(*) FROM {table} WHERE {where}', args).fetchone()[0]
        rows = db.execute(f'SELECT * FROM {table} WHERE {where} ORDER BY '+
                          ('revision,section,key' if section == 'history' else 'position')+' LIMIT ? OFFSET ?',
                          [*args, limit, offset]).fetchall()
        cache = {}
        items = [{k: r[k] for k in ('section', 'key', 'revision')} |
                 {'data': _unpack(json.loads(r['data']), db, cache) if r['data'] else None} for r in rows]
    return dict(items=items, total=count, next_offset=offset+len(items) if offset+len(items)<count else None)


def delete(project_id):
    with connection() as db:
        for table in ('codex_project_records', 'codex_project_history', 'codex_project_state'):
            db.execute(f'DELETE FROM {table} WHERE project_id=?', (project_id,))
