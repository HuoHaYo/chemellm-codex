import html
import hashlib
import json
import mimetypes
import secrets
import shutil
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request, UploadFile, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError
from app import config
from app.db import init_db, execute, one, rows, uid, now, connection
from app.calculations import Inputs, REGISTRY
from app.catalog import TOOLS, FORMULAS, EQUIPMENT
from app.competition import acceptance, PRICES
from app.documents import ingest, public_artifact, artifact_record
from app.diagrams import DEMO
from app.examples import EXAMPLES
from app.toolbox import SCHEMAS, run_tool, UncertainToolError
from app.agent import RunInput, create_run, dispatch, resume_run
from app.conversations import ConversationWrite, create_conversation, list_conversations, require_conversation, conversation_resources, attach_resource, attach_calculation, conversation_history
from app.model_settings import ModelSettings
from app.run_recovery import CallResolution, recover_runs, unknown_calls, reconcile_call
from app.design_data import EquipmentWriteInput, EquipmentReview, save_equipment, review_equipment, equipment_list, equipment_history
from app.datasets import DatasetInspectInput, inspect_dataset
from app.fulltext import snapshot_list, DocumentReadInput, read_document, CorrectTranscriptionInput, correct_transcription
from app.evidence import EvidenceQuery, ClaimReview, check_evidence, review_claim
from app.workflow import ParameterWrite, TaskWrite, WorkflowStart, AttemptResolution, save_parameter, save_task, study_status, study_export, start_execution, execution, cancel_execution, resume_execution, dispatch_execution, recover_interrupted, reconcile_attempt
from app.engineering import EngineeringQuery, engineering_status

@asynccontextmanager
async def lifespan(app):
    init_db()
    recover_runs()
    recover_interrupted()
    yield

app=FastAPI(title="ChemELLM",version="0.1.0",lifespan=lifespan)

@app.middleware("http")
async def access(request: Request,call_next):
    if request.url.path.startswith("/api/") and request.url.path not in {"/api/health","/api/login"} and config.AUTH_TOKEN:
        supplied=request.headers.get("Authorization","").removeprefix("Bearer ") or request.cookies.get("chemellm_session","")
        if not secrets.compare_digest(supplied,config.AUTH_TOKEN):
            return JSONResponse({"detail":"请输入工作台访问口令"},status_code=401)
    length=request.headers.get("content-length")
    if length:
        try:
            if int(length)>config.MAX_UPLOAD+1024*1024:
                return JSONResponse({"detail":"请求大小超过限制"},status_code=413)
        except ValueError:
            return JSONResponse({"detail":"无效 Content-Length"},status_code=400)
    if request.method not in {"GET","HEAD","OPTIONS"} and request.headers.get("origin"):
        from urllib.parse import urlparse
        if urlparse(request.headers["origin"]).netloc != request.headers.get("host"):
            return JSONResponse({"detail":"不允许跨站写入"},status_code=403)
    response=await call_next(request)
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="same-origin"
    response.headers["X-Frame-Options"]="SAMEORIGIN"
    if request.url.path.startswith("/api/") or request.url.path.endswith(('.html','.js','.css')) or request.url.path == '/':
        response.headers["Cache-Control"]="no-store"
    return response

@app.exception_handler(ValueError)
async def invalid(request,exc):
    return JSONResponse({"detail":str(exc)[:2000]},status_code=422)

class Login(Inputs):
    token: str = Field(max_length=500)

@app.exception_handler(UncertainToolError)
async def uncertain_tool_error(request:Request,exc:UncertainToolError):
    return JSONResponse({'detail':str(exc)[:2000],'outcome':'unknown','automatic_retry':False},status_code=409)

@app.post("/api/login")
def login(p:Login,request:Request):
    if config.AUTH_TOKEN and not secrets.compare_digest(config.AUTH_TOKEN,p.token):
        raise HTTPException(401,"口令不正确")
    response=JSONResponse({"ok":True})
    response.set_cookie("chemellm_session",p.token,httponly=True,samesite="strict",secure=request.url.scheme=="https",max_age=43200)
    return response

@app.get("/api/health")
def health():
    return {"status":"ok","version":"0.1.0","ui_revision":"conversations-2","capabilities":["conversations","editable_model_settings"]}

@app.get("/api/config")
def settings():
    cfg=config.model_config()
    return {"provider":cfg["provider"],"base_url":cfg["base_url"],"models":cfg["models"],"vision_model":cfg.get("vision_model", ""),"api_key_configured":bool(cfg["key"]),"reasoning_supported":cfg["reasoning_supported"],"timeout":cfg["timeout"],
            "iterations":cfg["iterations"],"office_preview":bool(shutil.which("libreoffice") or shutil.which("soffice")),
            "max_upload_MiB":25,"authenticated":bool(config.AUTH_TOKEN)}

@app.put('/api/config')
def save_settings(p:ModelSettings):
    config.save_model_settings(p)
    return settings()

def project(ident):
    row=one("SELECT * FROM projects WHERE id=?",(ident,))
    if not row:
        raise HTTPException(404,"项目不存在")
    return row

class ProjectInput(Inputs):
    name:str=Field(min_length=1,max_length=150)

@app.get("/api/projects")
def projects():
    return rows("SELECT * FROM projects ORDER BY created_at DESC")

@app.post("/api/projects")
def new_project(p:ProjectInput):
    ident=uid()
    execute("INSERT INTO projects VALUES(?,?,?)",(ident,p.name,now()))
    return project(ident)

@app.get("/api/projects/{ident}")
def project_detail(ident:str):
    return {**project(ident),"acceptance":acceptance(ident)}

@app.post("/api/projects/{ident}/import-competition")
def import_competition(ident:str):
    project(ident)
    source=config.ROOT/"26化工设计大赛题目.pdf"
    if not source.exists():
        raise HTTPException(404,"服务器未包含原赛题文件，请上传")
    existing=one("SELECT * FROM artifacts WHERE project_id=? AND name=?",(ident,source.name))
    if existing:
        return public_artifact(existing)
    return ingest(ident,source.name,source.read_bytes())

@app.post("/api/projects/{ident}/upload")
def upload(ident:str,file:UploadFile,conversation_id:str|None=None):
    project(ident)
    if conversation_id:
        with connection() as db:
            require_conversation(db,ident,conversation_id)
    try:
        result=ingest(ident,file.filename or "document",file.file.read(config.MAX_UPLOAD+1))
        if conversation_id:attach_resource(ident,conversation_id,result['id'])
        return result
    finally:
        file.file.close()

@app.get("/api/projects/{ident}/artifacts")
def artifacts(ident:str):
    project(ident)
    return [public_artifact(r) for r in rows("SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at DESC",(ident,))]

def artifact(ident):
    row=one("SELECT * FROM artifacts WHERE id=?",(ident,))
    if not row:
        raise HTTPException(404,"文件不存在")
    for field in ("path","preview_path"):
        if row[field] and not Path(row[field]).resolve().is_relative_to(config.ARTIFACTS):
            raise HTTPException(403,"无效文件路径")
    return row

@app.get("/api/artifacts/{ident}/download")
def download(ident:str):
    row=artifact(ident)
    return FileResponse(row["path"],filename=row["name"],media_type="application/octet-stream")

@app.get("/api/artifacts/{ident}/preview")
def preview(ident:str):
    row=artifact(ident)
    if row["preview_path"]:
        mime="image/svg+xml" if Path(row["preview_path"]).suffix==".svg" else "application/pdf"
        return FileResponse(row["preview_path"],media_type=mime,headers={"Content-Security-Policy":"sandbox; default-src 'none'; style-src 'unsafe-inline'"})
    if row["kind"] in {"pdf","svg","png","jpg","jpeg"}:
        mime={"pdf":"application/pdf","svg":"image/svg+xml","png":"image/png","jpg":"image/jpeg","jpeg":"image/jpeg"}[row["kind"]]
        return FileResponse(row["path"],media_type=mime,headers={"Content-Security-Policy":"sandbox; default-src 'none'; style-src 'unsafe-inline'"})
    warnings=json.loads(row["metadata"]).get("warnings",[])
    body=html.escape(row["text"] or "此格式提供原文件下载。")
    return HTMLResponse('<!doctype html><html lang="zh"><meta charset="utf-8"><title>文本预览</title><style>body{font:15px/1.8 sans-serif;padding:30px;background:#fafbf9;color:#213c36}pre{white-space:pre-wrap;overflow-wrap:anywhere}.note{color:#8e6218}</style><p class="note">文本预览，不保留原始排版。'+html.escape(" ".join(warnings))+'</p><pre>'+body+'</pre></html>',headers={"Content-Security-Policy":"sandbox; default-src 'none'; style-src 'unsafe-inline'"})

@app.get("/api/tools")
def tools():
    return {"tools":TOOLS,"schemas":{name:schema.model_json_schema() for name,schema in SCHEMAS.items()},"diagram_example":DEMO,"examples":EXAMPLES}

@app.get("/api/database")
def database():
    return {"formulas":FORMULAS,"equipment":EQUIPMENT,"prices":PRICES,"property_cache_count":one("SELECT COUNT(*) AS count FROM property_cache")["count"]}

class ToolInput(Inputs):
    arguments:dict=Field(default_factory=dict)
    conversation_id:str|None=Field(default=None,max_length=100)

@app.post("/api/projects/{ident}/tools/{name}")
def tool(ident:str,name:str,p:ToolInput):
    project(ident)
    if p.conversation_id:
        with connection() as db:
            require_conversation(db,ident,p.conversation_id)
    result=run_tool(ident,name,p.arguments)
    if p.conversation_id:attach_calculation(ident,p.conversation_id,result['calculation_id'])
    return result

@app.get("/api/projects/{ident}/calculations")
def calculations(ident:str,limit:int=Query(default=100,ge=1,le=500),offset:int=Query(default=0,ge=0)):
    project(ident)
    return calculation_records(ident,limit,offset)

def calculation_records(ident,limit=None,offset=0):
    query="SELECT * FROM calculations WHERE project_id=? ORDER BY created_at DESC,id"
    args=(ident,)
    if limit is not None:
        query+=" LIMIT ? OFFSET ?"
        args+=(limit,offset)
    result=rows(query,args)
    for row in result:
        row["input"]=json.loads(row["input"])
        row["result"]=json.loads(row["result"])
    return result

@app.get("/api/projects/{ident}/equipment")

def get_equipment(ident:str):
    project(ident)
    return equipment_list(ident)

@app.get("/api/projects/{ident}/study")
def get_study(ident:str):
    project(ident)
    return study_status(ident)

@app.post("/api/projects/{ident}/study/parameters")
def post_study_parameter(ident:str,p:ParameterWrite):
    project(ident)
    return save_parameter(ident,p)

@app.post("/api/projects/{ident}/study/tasks")
def post_study_task(ident:str,p:TaskWrite):
    project(ident)
    return save_task(ident,p)

@app.post("/api/projects/{ident}/study/executions")
def post_study_execution(ident:str,p:WorkflowStart):
    project(ident)
    result=start_execution(ident,p)
    dispatch_execution(result["id"])
    return result

@app.get("/api/study-executions/{ident}")
def get_study_execution(ident:str):return execution(ident)

@app.post("/api/study-attempts/{ident}/reconcile")
def reconcile_study_attempt(ident:str,p:AttemptResolution):return reconcile_attempt(ident,p)

@app.post("/api/study-executions/{ident}/cancel")
def cancel_study_execution(ident:str):return cancel_execution(ident)

@app.post("/api/study-executions/{ident}/resume")
def resume_study_execution(ident:str):
    result=resume_execution(ident)
    dispatch_execution(ident)
    return result

@app.get("/api/projects/{ident}/datasets/{artifact_id}")
def dataset_details(ident:str,artifact_id:str,sheet:str|None=None):
    project(ident)
    return inspect_dataset(ident,DatasetInspectInput(artifact_id=artifact_id,sheet=sheet))

@app.get("/api/projects/{ident}/documents")
def document_snapshots(ident:str):
    project(ident)
    return snapshot_list(ident)

@app.get("/api/projects/{ident}/documents/{snapshot_id}")
def document_blocks(ident:str,snapshot_id:str,offset:int=Query(default=0,ge=0),limit:int=Query(default=30,ge=1,le=100)):
    project(ident)
    return read_document(ident,DocumentReadInput(snapshot_id=snapshot_id,offset=offset,limit=limit))

@app.post("/api/projects/{ident}/documents/{snapshot_id}/transcription")
def human_transcription(ident:str,snapshot_id:str,p:CorrectTranscriptionInput):
    project(ident)
    return correct_transcription(ident,snapshot_id,p)

@app.get("/api/projects/{ident}/claims")
def project_claims(ident:str):
    project(ident)
    return check_evidence(ident,EvidenceQuery())

@app.post("/api/projects/{ident}/claims/{claim_id}/review")
def human_claim_review(ident:str,claim_id:str,p:ClaimReview):
    project(ident)
    return review_claim(ident,claim_id,p)

class DatasetExampleInput(Inputs):
    name:Literal["Misra1a","BoxBOD","DanWood"]="BoxBOD"

@app.post("/api/projects/{ident}/datasets/example")
def dataset_example(ident:str,p:DatasetExampleInput):
    project(ident)
    from app.benchmarks import example_csv
    content,reference=example_csv(p.name)
    result=ingest(ident,p.name+".csv",content)
    details={**result["metadata"],"reference":{"source_url":reference["url"],"source_sha256":reference["sha256"],"topic":reference["topic"],"classification":"public_observed_reference_data","x_unit":reference["x_unit"],"y_unit":reference["y_unit"]}}
    execute("UPDATE artifacts SET metadata=? WHERE id=?",(json.dumps(details,ensure_ascii=False),result["id"]))
    return public_artifact(one("SELECT * FROM artifacts WHERE id=?",(result["id"],)))

@app.post("/api/projects/{ident}/equipment")
def post_equipment(ident:str,p:EquipmentWriteInput):
    project(ident)
    return save_equipment(ident,p)

@app.post("/api/projects/{ident}/equipment/{record_id}/review")
def post_equipment_review(ident:str,record_id:str,p:EquipmentReview):
    project(ident)
    return review_equipment(ident,record_id,p)

@app.get("/api/projects/{ident}/equipment/{record_id}/history")
def get_equipment_history(ident:str,record_id:str):
    project(ident)
    return equipment_history(ident,record_id)

@app.get("/api/projects/{ident}/acceptance")
def get_acceptance(ident:str):
    project(ident)
    return acceptance(ident)

class RequirementUpdate(Inputs):
    status:Literal["missing","draft","verified"]
    evidence:list[str]=Field(default_factory=list,max_length=40)
    notes:str=Field(default="",max_length=3000)

@app.put("/api/projects/{ident}/requirements/{code}")
def update_requirement(ident:str,code:str,p:RequirementUpdate):
    project(ident)
    if not one("SELECT id FROM requirements WHERE project_id=? AND code=?",(ident,code)):
        raise HTTPException(404,"验收项不存在")
    if p.status=="verified" and (not p.evidence or len(p.notes.strip())<10):
        raise ValueError("人工审核通过必须关联证据文件并记录至少10字的校核说明")
    for item in p.evidence:
        source=one("SELECT id,metadata FROM artifacts WHERE id=? AND project_id=?",(item,ident))
        if not source:
            raise ValueError("证据必须是当前项目文件")
        if p.status=="verified" and json.loads(source["metadata"]).get("workflow_freshness")=="stale":
            raise ValueError("证据上游已变化，请重算并关联当前版本后再审核")
    execute("UPDATE requirements SET status=?,evidence=?,notes=? WHERE project_id=? AND code=?",(p.status,json.dumps(p.evidence),p.notes,ident,code))
    return get_acceptance(ident)

@app.get("/api/projects/{ident}/messages")
def messages(ident:str,conversation_id:str|None=None):
    project(ident)
    if conversation_id:
        with connection() as db:
            require_conversation(db,ident,conversation_id)
        return rows('SELECT m.* FROM messages m JOIN conversation_messages cm ON cm.message_id=m.id WHERE m.project_id=? AND cm.conversation_id=? ORDER BY m.created_at',(ident,conversation_id))
    return rows("SELECT * FROM messages WHERE project_id=? ORDER BY created_at",(ident,))

@app.get('/api/projects/{ident}/conversations')
def get_conversations(ident:str):
    project(ident)
    return list_conversations(ident)

@app.post('/api/projects/{ident}/conversations')
def post_conversation(ident:str,p:ConversationWrite):
    project(ident)
    return create_conversation(ident,p)

@app.patch('/api/projects/{ident}/conversations/{conversation_id}')
def edit_conversation(ident:str,conversation_id:str,p:ConversationWrite):
    project(ident)
    if not p.title.strip():raise ValueError('会话名称不能为空')
    with connection() as db:
        require_conversation(db,ident,conversation_id)
        if p.task_key and not db.execute('SELECT id FROM study_tasks WHERE project_id=? AND key=?',(ident,p.task_key)).fetchone():
            raise ValueError('研究任务不属于当前项目')
        db.execute('UPDATE conversations SET title=?,task_key=?,updated_at=? WHERE id=?',(p.title.strip(),p.task_key,now(),conversation_id))
        return require_conversation(db,ident,conversation_id)

@app.get('/api/projects/{ident}/conversations/{conversation_id}/resources')
def get_conversation_resources(ident:str,conversation_id:str):
    project(ident)
    return conversation_resources(ident,conversation_id)

@app.get('/api/projects/{ident}/conversations/{conversation_id}/history')
def get_conversation_history(ident:str,conversation_id:str,limit:int=Query(default=30,ge=1,le=100),offset:int=Query(default=0,ge=0)):
    project(ident)
    return conversation_history(ident,conversation_id,limit,offset)

@app.post('/api/projects/{ident}/conversations/{conversation_id}/resources/{artifact_id}')
def link_conversation_resource(ident:str,conversation_id:str,artifact_id:str):
    project(ident)
    return attach_resource(ident,conversation_id,artifact_id)

@app.get('/api/projects/{ident}/engineering')
def get_engineering(ident:str,include_values:bool=True,run_id:str|None=None):
    project(ident)
    return engineering_status(ident,EngineeringQuery(include_values=include_values),run_id=run_id)

@app.post("/api/projects/{ident}/runs")
def new_run(ident:str,p:RunInput):
    project(ident)
    run_id=create_run(ident,p)
    dispatch(run_id)
    return {"id":run_id,"status":"queued"}

@app.get("/api/projects/{ident}/runs")
def list_runs(ident:str,conversation_id:str|None=None):
    project(ident)
    if conversation_id:
        with connection() as db:
            require_conversation(db,ident,conversation_id)
        result=rows('SELECT r.id,r.status,r.request,r.result,r.created_at,r.updated_at FROM runs r JOIN conversation_runs cr ON cr.run_id=r.id WHERE r.project_id=? AND cr.conversation_id=? ORDER BY r.created_at DESC LIMIT 30',(ident,conversation_id))
    else:result=rows("SELECT id,status,request,result,created_at,updated_at FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT 30",(ident,))
    for item in result:
        item['needs_reconciliation']=any(a['status']=='unknown' for a in json.loads(item.pop('result')).get('tool_attempts',[]))
    return result

@app.get("/api/runs/{ident}")
def get_run(ident:str):
    row=one("SELECT * FROM runs WHERE id=?",(ident,))
    if not row:
        raise HTTPException(404,"任务不存在")
    state=json.loads(row.pop("result"))
    row["result"]={k:v for k,v in state.items() if k!="messages"}
    row["request"]=json.loads(row["request"])
    row['unknown_calls']=unknown_calls(ident)
    row["events"]=rows("SELECT * FROM events WHERE run_id=? ORDER BY id",(ident,))
    for item in row["events"]:
        item["payload"]=json.loads(item["payload"])
    return row

class ResumeInput(Inputs):
    answer:str=Field(default="",max_length=20000)

@app.post('/api/runs/{ident}/tool-attempts/{attempt_id}/reconcile')
def reconcile_run_call(ident:str,attempt_id:str,p:CallResolution):
    return reconcile_call(ident,attempt_id,p)

@app.post("/api/runs/{ident}/resume")
def resume(ident:str,p:ResumeInput):
    resume_run(ident,p.answer)
    dispatch(ident)
    return {"id":ident,"status":"queued"}

@app.post("/api/runs/{ident}/cancel")
def cancel(ident:str):
    if not one("SELECT id FROM runs WHERE id=?",(ident,)):
        raise HTTPException(404,"任务不存在")
    execute("UPDATE runs SET status='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running','awaiting_user','iteration_limit')",(now(),ident))
    return get_run(ident)

@app.post("/api/projects/{ident}/bundle")
def bundle(ident:str,terminal_run_id:str|None=None,terminal_snapshot_token:str|None=None,
           terminal_snapshot_event_id:int|None=None):
    item=project(ident)
    folder=config.ARTIFACTS/uid()
    folder.mkdir()
    path=folder/"project-deliverables.zip"
    evidence=acceptance(ident)
    records=rows("SELECT * FROM artifacts WHERE project_id=? AND kind!='zip'",(ident,))
    design_records=equipment_list(ident)
    manifest={"project":item,"acceptance":evidence,"artifacts":[public_artifact(r) for r in records],"calculations":calculation_records(ident),"equipment":design_records,"equipment_history":{r["id"]:equipment_history(ident,r["id"]) for r in design_records},
              "document_snapshots":rows("SELECT * FROM document_snapshots WHERE project_id=?",(ident,)),"research_evidence":check_evidence(ident,EvidenceQuery()),"study_workflow":study_export(ident),"status":"review_required"}
    run_inputs=rows('SELECT * FROM engineering_run_inputs WHERE project_id=? ORDER BY created_at',(ident,))
    latest_run=run_inputs[-1]['run_id'] if run_inputs else None
    manifest['engineering']=engineering_status(ident,run_id=latest_run)
    manifest['engineering_history']={table:rows(f'SELECT * FROM {table} WHERE project_id=?',(ident,)) for table in ['engineering_object_revisions','engineering_deliverable_revisions','engineering_requirement_sources']}
    manifest['runs']=rows('SELECT * FROM runs WHERE project_id=? ORDER BY created_at',(ident,))
    manifest['events']=rows('SELECT e.* FROM events e JOIN runs r ON r.id=e.run_id WHERE r.project_id=? ORDER BY e.id',(ident,))
    manifest['frozen_runs']=[{**r,'snapshot':json.loads(r['snapshot'])} for r in run_inputs]
    terminal_evidence=None
    if terminal_run_id is not None:
        if not terminal_snapshot_token or terminal_snapshot_event_id is None:
            raise ValueError('终态封包缺少执行器快照标识')
        terminal_run=one('SELECT * FROM runs WHERE id=? AND project_id=?',(terminal_run_id,ident))
        if not terminal_run or json.loads(terminal_run['request']).get('mode')!='engineering':
            raise ValueError('终态封包运行不属于当前工程项目')
        if terminal_run['status'] not in {'needs_review','budget_exhausted','failed','cancelled','interrupted','completed'}:
            raise ValueError('工程运行尚未进入可封包的终态')
        terminal_events=rows('SELECT * FROM events WHERE run_id=? ORDER BY id',(terminal_run_id,))
        snapshot_event=next((item for item in terminal_events if item['id']==terminal_snapshot_event_id and item['type']=='executor_terminal_snapshot'),None)
        if not snapshot_event or json.loads(snapshot_event['payload']).get('snapshot_token')!=terminal_snapshot_token:
            raise ValueError('执行器终态快照事件缺失或标识不匹配')
        terminal_evidence={
            'run_id':terminal_run_id,
            'status':terminal_run['status'],
            'updated_at':terminal_run['updated_at'],
            'request':json.loads(terminal_run['request']),
            'checkpoint':json.loads(terminal_run['result']),
            'checkpoint_sha256':hashlib.sha256(terminal_run['result'].encode()).hexdigest(),
            'events':[{**item,'payload':json.loads(item['payload'])} for item in terminal_events],
            'snapshot_event_id':terminal_snapshot_event_id,
            'snapshot_token':terminal_snapshot_token,
            'engineering_acceptance':engineering_status(ident,run_id=terminal_run_id),
            'classification':'executor_terminal_evidence_not_model_authored_engineering_deliverable',
            'professional_review':'not_reviewed',
        }
        manifest['executor_terminal_evidence']=terminal_evidence
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        for frozen in manifest['frozen_runs']:
            src=Path(frozen['snapshot']['frozen_archive']).resolve()
            if src.is_relative_to(config.DATA.resolve()/'engineering-runs') and src.is_file():
                archive.write(src,'frozen/'+frozen['run_id']+'/inputs-platform.zip')
        for record in records:
            src=Path(record["path"]).resolve()
            if src.is_relative_to(config.ARTIFACTS) and src.is_file():
                archive.write(src,"files/"+record["id"]+"/"+src.name)
        for source in (config.ROOT/"app").glob("*.py"):
            archive.write(source,"reproduce/app/"+source.name)
        archive.write(config.ROOT/"pyproject.toml","reproduce/pyproject.toml")
        archive.write(config.ROOT/"scripts"/"replay.py","reproduce/replay.py")
        archive.write(config.ROOT/"scripts"/"check_evidence_bundle.py","reproduce/check_evidence_bundle.py")
        install_target=".[simulation]" if any(r["tool"] in {"cantera_equilibrium","cantera_reactor"} for r in manifest["calculations"]) else "."
        archive.writestr("reproduce/README.txt",f'python -m pip install "{install_target}"\npython replay.py\npython check_evidence_bundle.py\nUses ../manifest.json. Numerical reproducibility and quote integrity do not establish scientific validity or engineering approval.\n')
    metadata={"status":"review_required","mandatory_verified":evidence["verified"],"mandatory_total":evidence["mandatory"]}
    if terminal_evidence is not None:
        metadata['executor_terminal_evidence']={key:terminal_evidence[key] for key in
            ('run_id','status','updated_at','checkpoint_sha256','snapshot_event_id','snapshot_token','classification','professional_review')}
        metadata['status']='executor_terminal_evidence_review_required'
    return artifact_record(ident,path,name=path.name,metadata=metadata)

app.mount("/",StaticFiles(directory=config.ROOT/"web",html=True),name="web")
