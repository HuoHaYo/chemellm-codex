"""Project conversations with isolated transcripts and explicit resource references."""
import json
from pydantic import Field
from app.calculations import Inputs
from app.db import connection, now, uid
from app.documents import public_artifact


class ConversationWrite(Inputs):
    title: str = Field(default="新会话", min_length=1, max_length=150)
    task_key: str | None = Field(default=None, max_length=60)


def migrate_legacy(db):
    projects = db.execute("SELECT DISTINCT project_id FROM messages WHERE id NOT IN (SELECT message_id FROM conversation_messages) UNION SELECT DISTINCT project_id FROM runs WHERE id NOT IN (SELECT run_id FROM conversation_runs)").fetchall()
    for item in projects:
        legacy_conversation(db, item['project_id'])


def migrate_project_history(db):
    """Expose records predating conversation support without manufacturing chat messages."""
    for project in db.execute('SELECT id FROM projects').fetchall():
        project_id = project['id']
        ident = 'history-' + project_id
        if db.execute('SELECT 1 FROM conversation_history WHERE conversation_id=?', (ident,)).fetchone():
            continue
        earliest = db.execute("SELECT MIN(created_at) AS stamp FROM conversations WHERE project_id=? AND id<>?", (project_id, ident)).fetchone()['stamp']
        cutoff = earliest or now()
        bounds = db.execute('''SELECT MIN(created_at) AS first,MAX(created_at) AS last FROM (
            SELECT created_at FROM calculations WHERE project_id=? AND created_at<?
            UNION ALL SELECT created_at FROM artifacts WHERE project_id=? AND created_at<?
        )''', (project_id, cutoff, project_id, cutoff)).fetchone()
        if not bounds['first']:
            continue
        db.execute('INSERT OR IGNORE INTO conversations VALUES(?,?,?,?,?,?)', (ident, project_id, '项目历史记录', None, bounds['first'], bounds['last']))
        db.execute('INSERT INTO conversation_history VALUES(?,?)', (ident, cutoff))


def legacy_conversation(db, project_id):
    ident = 'legacy-' + project_id
    timestamp = now()
    db.execute("INSERT OR IGNORE INTO conversations VALUES(?,?,?,?,?,?)", (ident, project_id, '历史会话', None, timestamp, timestamp))
    db.execute("INSERT OR IGNORE INTO conversation_messages SELECT id,? FROM messages WHERE project_id=? AND id NOT IN (SELECT message_id FROM conversation_messages)", (ident, project_id))
    db.execute("INSERT OR IGNORE INTO conversation_runs SELECT id,? FROM runs WHERE project_id=? AND id NOT IN (SELECT run_id FROM conversation_runs)", (ident, project_id))
    return ident


def require_conversation(db, project_id, ident):
    item = db.execute('SELECT * FROM conversations WHERE id=? AND project_id=?', (ident, project_id)).fetchone()
    if not item:
        raise ValueError('会话不属于当前项目或已不存在')
    return dict(item)


def create_conversation(project_id, value):
    with connection() as db:
        if value.task_key and not db.execute('SELECT id FROM study_tasks WHERE project_id=? AND key=?', (project_id, value.task_key)).fetchone():
            raise ValueError('研究任务不属于当前项目')
        title = value.title.strip()
        if not title:
            raise ValueError('会话名称不能为空')
        ident, timestamp = uid(), now()
        db.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?)', (ident, project_id, title, value.task_key, timestamp, timestamp))
        return require_conversation(db, project_id, ident)


def list_conversations(project_id):
    with connection() as db:
        return [dict(row) for row in db.execute('''SELECT c.*,t.config AS task_config,h.cutoff AS history_cutoff,
            (SELECT COUNT(*) FROM conversation_messages cm WHERE cm.conversation_id=c.id) AS message_count,
            (SELECT COUNT(*) FROM conversation_calculations cc WHERE cc.conversation_id=c.id) +
                CASE WHEN h.cutoff IS NULL THEN 0 ELSE (SELECT COUNT(*) FROM calculations r WHERE r.project_id=c.project_id AND r.created_at<h.cutoff) END AS calculation_count,
            (SELECT r.status FROM runs r JOIN conversation_runs cr ON cr.run_id=r.id WHERE cr.conversation_id=c.id ORDER BY r.created_at DESC LIMIT 1) AS latest_status
            FROM conversations c LEFT JOIN study_tasks t ON t.project_id=c.project_id AND t.key=c.task_key
            LEFT JOIN conversation_history h ON h.conversation_id=c.id
            WHERE c.project_id=? ORDER BY c.updated_at DESC,c.id''', (project_id,))]


def attach_calculation(project_id, conversation_id, calculation_id):
    with connection() as db:
        require_conversation(db, project_id, conversation_id)
        record = db.execute('SELECT result FROM calculations WHERE id=? AND project_id=?', (calculation_id, project_id)).fetchone()
        if not record:
            raise ValueError('计算记录不属于当前项目')
        db.execute('INSERT OR IGNORE INTO conversation_calculations VALUES(?,?)', (calculation_id, conversation_id))
        for artifact in json.loads(record['result']).get('artifacts', []):
            if isinstance(artifact, dict) and db.execute('SELECT 1 FROM artifacts WHERE id=? AND project_id=?', (artifact.get('id'), project_id)).fetchone():
                db.execute('INSERT OR IGNORE INTO conversation_artifacts VALUES(?,?,?)', (conversation_id, artifact['id'], now()))
        db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now(), conversation_id))


def conversation_history(project_id, conversation_id, limit=30, offset=0):
    with connection() as db:
        conversation = require_conversation(db, project_id, conversation_id)
        history = db.execute('SELECT cutoff FROM conversation_history WHERE conversation_id=?', (conversation_id,)).fetchone()
        cutoff = history['cutoff'] if history else ''
        where = '''r.project_id=? AND (r.id IN (SELECT calculation_id FROM conversation_calculations WHERE conversation_id=?)
            OR r.created_at<?)'''
        args = (project_id, conversation_id, cutoff)
        total = db.execute('SELECT COUNT(*) AS n FROM calculations r WHERE '+where, args).fetchone()['n']
        records = [dict(item) for item in db.execute('SELECT r.* FROM calculations r WHERE '+where+' ORDER BY r.created_at,r.id LIMIT ? OFFSET ?', (*args, limit, offset))]
        for record in records:
            record['input'] = json.loads(record['input'])
            record['result'] = json.loads(record['result'])
        artifacts = [public_artifact(dict(item)) for item in db.execute('''SELECT a.* FROM artifacts a WHERE a.project_id=?
            AND (a.created_at<? OR a.id IN (SELECT artifact_id FROM conversation_artifacts WHERE conversation_id=?)) ORDER BY a.created_at,a.id''', (project_id, cutoff, conversation_id))]
        return {'conversation': conversation, 'is_project_history': bool(history), 'calculations': records,
                'total_calculations': total, 'offset': offset, 'limit': limit, 'artifacts': artifacts}


def add_message(project_id, run_id, role, content, db=None):
    if db is None:
        with connection() as opened:
            return add_message(project_id, run_id, role, content, opened)
    message_id, timestamp = uid(), now()
    db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', (message_id, project_id, role, content, timestamp))
    linked = db.execute('SELECT conversation_id FROM conversation_runs WHERE run_id=?', (run_id,)).fetchone()
    conversation_id = linked['conversation_id'] if linked else legacy_conversation(db, project_id)
    db.execute('INSERT OR IGNORE INTO conversation_messages VALUES(?,?)', (message_id, conversation_id))
    db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (timestamp, conversation_id))


def attach_resource(project_id, conversation_id, artifact_id):
    with connection() as db:
        require_conversation(db, project_id, conversation_id)
        artifact = db.execute('SELECT * FROM artifacts WHERE id=? AND project_id=?', (artifact_id, project_id)).fetchone()
        if not artifact:
            raise ValueError('资源文件不属于当前项目')
        db.execute('INSERT OR IGNORE INTO conversation_artifacts VALUES(?,?,?)', (conversation_id, artifact_id, now()))
        return public_artifact(dict(artifact))


def conversation_resources(project_id, conversation_id):
    with connection() as db:
        conversation = require_conversation(db, project_id, conversation_id)
        related_ids = [row['id'] for row in db.execute('SELECT id FROM conversations WHERE project_id=? AND task_key=?', (project_id, conversation['task_key']))] if conversation['task_key'] else [conversation_id]
        placeholders = ','.join('?' for _ in related_ids)
        references = {}
        for related_id in related_ids:
            history = db.execute('SELECT cutoff FROM conversation_history WHERE conversation_id=?', (related_id,)).fetchone()
            if history:
                for record in db.execute('SELECT id FROM artifacts WHERE project_id=? AND created_at<?', (project_id, history['cutoff'])):
                    references.setdefault(record['id'], set()).add('项目历史成果')
        def walk(value, relation):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {'artifact_id', 'id'} and isinstance(item, str):
                        references.setdefault(item, set()).add(relation)
                    elif key == 'artifact_ids' and isinstance(item, list):
                        for ident in item:
                            if isinstance(ident, str):
                                references.setdefault(ident, set()).add(relation)
                    walk(item, relation)
            elif isinstance(value, list):
                for item in value:
                    walk(item, relation)
        for row in db.execute('SELECT artifact_id FROM conversation_artifacts WHERE conversation_id IN (' + placeholders + ')', related_ids):
            references.setdefault(row['artifact_id'], set()).add('任务资料' if conversation['task_key'] else '会话资料')
        for row in db.execute('SELECT r.result FROM runs r JOIN conversation_runs cr ON cr.run_id=r.id WHERE cr.conversation_id IN (' + placeholders + ')', related_ids):
            state = json.loads(row['result'])
            for attempt in state.get('tool_attempts', []):
                walk(attempt.get('input', {}), '会话引用')
                calculation_id = attempt.get('calculation_id')
                record = db.execute('SELECT result FROM calculations WHERE id=? AND project_id=?', (calculation_id, project_id)).fetchone() if calculation_id else None
                if record:
                    walk(json.loads(record['result']), '会话成果')
            walk(state.get('messages', []), '会话引用')
        for row in db.execute("SELECT e.payload FROM events e JOIN conversation_runs cr ON cr.run_id=e.run_id WHERE cr.conversation_id IN (" + placeholders + ") AND e.type='tool_result'", related_ids):
            walk(json.loads(row['payload']), '会话成果')
        task = None
        if conversation['task_key']:
            item = db.execute('SELECT * FROM study_tasks WHERE project_id=? AND key=?', (project_id, conversation['task_key'])).fetchone()
            if item:
                task = dict(item)
                task['config'] = json.loads(task['config'])
                walk(task['config'].get('arguments', {}), '任务输入')
                attempt = db.execute('SELECT result FROM study_attempts WHERE id=?', (task['latest_attempt_id'],)).fetchone()
                if attempt:
                    walk(json.loads(attempt['result']), '任务成果')
        artifacts = []
        for item in db.execute('SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at DESC', (project_id,)):
            if item['id'] in references:
                artifacts.append({**public_artifact(dict(item)), 'relations': sorted(references[item['id']])})
        return {'conversation': conversation, 'task': task, 'artifacts': artifacts}
