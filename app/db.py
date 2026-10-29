import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
from app.config import DATA

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid4().hex

@contextmanager
def connection():
    db = sqlite3.connect(DATA / "chemellm.sqlite3", timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), name TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL, preview_path TEXT, text TEXT, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requirements(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), code TEXT NOT NULL, title TEXT NOT NULL, page INTEGER, mandatory INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'missing', evidence TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '', UNIQUE(project_id,code));
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), status TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT REFERENCES runs(id), type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS calculations(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), tool TEXT NOT NULL, input TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS property_cache(id TEXT PRIMARY KEY, compound TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS equipment_records(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), tag TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', review_notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,tag));
        CREATE TABLE IF NOT EXISTS equipment_revisions(record_id TEXT NOT NULL REFERENCES equipment_records(id), revision INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(record_id,revision));
        CREATE TABLE IF NOT EXISTS equipment_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,record_id TEXT NOT NULL REFERENCES equipment_records(id),revision INTEGER NOT NULL,status TEXT NOT NULL,notes TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_snapshots(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),artifact_id TEXT NOT NULL REFERENCES artifacts(id),source_sha256 TEXT NOT NULL,content_sha256 TEXT NOT NULL,extractor_versions TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research_claims(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),text TEXT NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claim_revisions(claim_id TEXT NOT NULL REFERENCES research_claims(id),revision INTEGER NOT NULL,text TEXT NOT NULL,change TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(claim_id,revision));
        CREATE TABLE IF NOT EXISTS claim_evidence(id TEXT PRIMARY KEY,claim_id TEXT NOT NULL REFERENCES research_claims(id),project_id TEXT NOT NULL REFERENCES projects(id),snapshot_id TEXT NOT NULL REFERENCES document_snapshots(id),block_id TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claim_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,claim_id TEXT NOT NULL REFERENCES research_claims(id),revision INTEGER NOT NULL,decision TEXT NOT NULL,reviewer TEXT NOT NULL,notes TEXT NOT NULL,ocr_checked INTEGER NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS study_parameters(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),key TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(project_id,key));
        CREATE TABLE IF NOT EXISTS study_parameter_revisions(parameter_id TEXT NOT NULL REFERENCES study_parameters(id),revision INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(parameter_id,revision));
        CREATE TABLE IF NOT EXISTS study_tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),key TEXT NOT NULL,revision INTEGER NOT NULL,config TEXT NOT NULL,status TEXT NOT NULL,latest_attempt_id TEXT,reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(project_id,key));
        CREATE TABLE IF NOT EXISTS study_task_revisions(task_id TEXT NOT NULL REFERENCES study_tasks(id),revision INTEGER NOT NULL,config TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(task_id,revision));
        CREATE TABLE IF NOT EXISTS study_executions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),request TEXT NOT NULL,status TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS study_attempts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES study_tasks(id),execution_id TEXT NOT NULL REFERENCES study_executions(id),task_revision INTEGER NOT NULL,input TEXT NOT NULL,sources TEXT NOT NULL,status TEXT NOT NULL,calculation_id TEXT,result TEXT NOT NULL,error TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT);
        CREATE TABLE IF NOT EXISTS study_events(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL REFERENCES projects(id),execution_id TEXT,task_id TEXT,type TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS study_review_invalidations(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,record_id TEXT NOT NULL,previous TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS engineering_objects(project_id TEXT NOT NULL REFERENCES projects(id),key TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(project_id,key));
        CREATE TABLE IF NOT EXISTS engineering_object_revisions(project_id TEXT NOT NULL REFERENCES projects(id),key TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(project_id,key,revision));
        CREATE TABLE IF NOT EXISTS engineering_deliverables(project_id TEXT NOT NULL REFERENCES projects(id),key TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(project_id,key));
        CREATE TABLE IF NOT EXISTS engineering_deliverable_revisions(project_id TEXT NOT NULL REFERENCES projects(id),key TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(project_id,key,revision));
        CREATE TABLE IF NOT EXISTS engineering_run_inputs(run_id TEXT PRIMARY KEY REFERENCES runs(id),project_id TEXT NOT NULL REFERENCES projects(id),snapshot TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS engineering_requirement_sources(project_id TEXT NOT NULL REFERENCES projects(id),code TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(project_id,code));
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),title TEXT NOT NULL,task_key TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversation_messages(message_id TEXT PRIMARY KEY REFERENCES messages(id),conversation_id TEXT NOT NULL REFERENCES conversations(id));
        CREATE TABLE IF NOT EXISTS conversation_runs(run_id TEXT PRIMARY KEY REFERENCES runs(id),conversation_id TEXT NOT NULL REFERENCES conversations(id));
        CREATE TABLE IF NOT EXISTS conversation_artifacts(conversation_id TEXT NOT NULL REFERENCES conversations(id),artifact_id TEXT NOT NULL REFERENCES artifacts(id),created_at TEXT NOT NULL,PRIMARY KEY(conversation_id,artifact_id));
        CREATE TABLE IF NOT EXISTS conversation_calculations(calculation_id TEXT PRIMARY KEY REFERENCES calculations(id),conversation_id TEXT NOT NULL REFERENCES conversations(id));
        CREATE TABLE IF NOT EXISTS conversation_history(conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),cutoff TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS conversation_project ON conversations(project_id,updated_at);
        CREATE INDEX IF NOT EXISTS conversation_message_lookup ON conversation_messages(conversation_id);
        CREATE INDEX IF NOT EXISTS conversation_run_lookup ON conversation_runs(conversation_id);
        """)
        from app.conversations import migrate_legacy, migrate_project_history
        migrate_legacy(db)
        migrate_project_history(db)

def rows(sql, args=()):
    with connection() as db:
        return [dict(row) for row in db.execute(sql, args).fetchall()]

def one(sql, args=()):
    result = rows(sql, args)
    return result[0] if result else None

def execute(sql, args=()):
    with connection() as db:
        db.execute(sql, args)

def event(run_id, kind, payload):
    execute("INSERT INTO events(run_id,type,payload,created_at) VALUES(?,?,?,?)", (run_id, kind, json.dumps(payload, ensure_ascii=False, allow_nan=False), now()))
