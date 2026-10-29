"""Versioned project inputs and dependency-aware, checkpointed tool execution."""
import hashlib
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import Field, model_validator
from app.db import connection, now, one, rows, uid
from app.schemas import Inputs
from app.design_data import units

KEY=r"^[A-Za-z][A-Za-z0-9_-]{0,59}$"


def encoded(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True)


class ParameterWrite(Inputs):
    key:str=Field(pattern=KEY)
    label:str=Field(min_length=1,max_length=120)
    value:float
    unit:str=Field(min_length=1,max_length=80)
    provenance:Literal["assumption","experimental","predicted","literature"]="assumption"
    conditions:str=Field(min_length=1,max_length=2000)
    source:str=Field(default="",max_length=2000)
    uncertainty:float|None=Field(default=None,ge=0)
    expected_revision:int|None=Field(default=None,ge=1)

    @model_validator(mode="after")
    def source_required(self):
        if self.provenance!="assumption" and not self.source.strip():raise ValueError("非假设参数必须填写来源")
        return self


class InputBinding(Inputs):
    target:str=Field(min_length=1,max_length=200,description="输入JSON指针，如/value或/streams/0/supply_C")
    kind:Literal["parameter","task"]
    key:str=Field(pattern=KEY)
    path:str=Field(default="",max_length=300,description="上游任务结果JSON指针；参数只取value")
    unit:str=Field(default="",max_length=80,description="目标单位；任务来源注明结果字段单位，不作自动推断")
    render_as:Literal["value","text"]="value"
    prefix:str=Field(default="",max_length=2000)
    suffix:str=Field(default="",max_length=2000)


class TaskWrite(Inputs):
    key:str=Field(pattern=KEY)
    title:str=Field(min_length=1,max_length=150)
    tool:str=Field(min_length=1,max_length=80)
    arguments:dict=Field(default_factory=dict)
    bindings:list[InputBinding]=Field(default_factory=list,max_length=100)
    depends_on:list[str]=Field(default_factory=list,max_length=100)
    expected_revision:int|None=Field(default=None,ge=1)

    @model_validator(mode="after")
    def unique_targets(self):
        targets=[b.target for b in self.bindings]
        if len(set(targets))!=len(targets):raise ValueError("同一输入字段不能重复绑定")
        for a in targets:
            pointer(a)
            if any(b!=a and b.startswith(a+"/") for b in targets):raise ValueError("不能同时绑定父字段和子字段")
        return self


class WorkflowStart(Inputs):
    task_keys:list[str]=Field(min_length=1,max_length=100)
    max_tools:int=Field(default=30,ge=1,le=100)
    max_seconds:float=Field(default=300,ge=1,le=3600)


class AttemptResolution(Inputs):
    decision:Literal["retry","adopt"]
    reviewer:str=Field(min_length=2,max_length=100)
    notes:str=Field(min_length=10,max_length=3000)
    calculation_id:str|None=Field(default=None,max_length=100)

    @model_validator(mode="after")
    def selected_result(self):
        if self.decision=="adopt" and not self.calculation_id:raise ValueError("采用已有结果须选择计算记录")
        if self.decision=="retry" and self.calculation_id:raise ValueError("确认重试不能同时采用旧结果")
        return self


def pointer(path):
    if not path:return []
    if not path.startswith("/"):raise ValueError("字段路径必须是JSON指针，如/value")
    parts=path[1:].split("/")
    for part in parts:
        for index,char in enumerate(part):
            if char=="~" and (index+1==len(part) or part[index+1] not in "01"):raise ValueError("JSON指针转义无效")
    return [p.replace("~1","/").replace("~0","~") for p in parts]


def at(value,path):
    for part in pointer(path):
        if isinstance(value,list):
            if not part.isdigit() or str(int(part))!=part:raise ValueError("数组路径需要有效索引")
            value=value[int(part)]
        elif isinstance(value,dict):value=value[part]
        else:raise ValueError("路径指向非容器字段")
    return value


def put(value,path,replacement):
    parts=pointer(path)
    if not parts:raise ValueError("绑定目标不能替换整个输入")
    parent=value
    for part in parts[:-1]:
        if isinstance(parent,list):parent=parent[int(part)]
        else:parent=parent[part]
    if isinstance(parent,list):parent[int(parts[-1])]=replacement
    elif isinstance(parent,dict):parent[parts[-1]]=replacement
    else:raise ValueError("绑定目标不是容器")


def log(db,project_id,kind,payload,execution_id=None,task_id=None):
    db.execute("INSERT INTO study_events(project_id,execution_id,task_id,type,payload,created_at) VALUES(?,?,?,?,?,?)",
               (project_id,execution_id,task_id,kind,encoded(payload),now()))


def parameter_row(row):
    return {**{k:v for k,v in dict(row).items() if k!="data"},"data":json.loads(row["data"])}


def task_row(db,row):
    result={**dict(row),"config":json.loads(row["config"])}
    latest=db.execute("SELECT * FROM study_attempts WHERE id=?",(row["latest_attempt_id"],)).fetchone()
    result["latest_attempt"]=attempt_row(latest) if latest else None
    return result


def attempt_row(row):
    result=dict(row)
    for k in ["input","sources","result"]:result[k]=json.loads(result[k])
    return result


def dependencies(config):return set(config["depends_on"])|{b["key"] for b in config["bindings"] if b["kind"]=="task"}


def invalidate(db,project_id,roots,reason):
    """Invalidate all downstream work and linked acceptance; retain old files/history."""
    records=list(db.execute("SELECT * FROM study_tasks WHERE project_id=?",(project_id,)))
    changed=set(roots)
    while True:
        more={r["key"] for r in records if dependencies(json.loads(r["config"]))&changed}
        if more<=changed:break
        changed|=more
    artifact_ids=set();calculation_ids=set()
    for record in records:
        if record["key"] not in changed:continue
        db.execute("UPDATE study_tasks SET status='stale',reason=?,updated_at=? WHERE id=?",(reason,now(),record["id"]))
        for attempt in db.execute("SELECT id,calculation_id,result FROM study_attempts WHERE task_id=?",(record["id"],)):
            if attempt["calculation_id"]:calculation_ids.add(attempt["calculation_id"])
            artifact_ids.update(a["id"] for a in json.loads(attempt["result"]).get("artifacts",[]))
        log(db,project_id,"invalidated",{"reason":reason},task_id=record["id"])
    for ident in artifact_ids:
        record=db.execute("SELECT metadata FROM artifacts WHERE id=? AND project_id=?",(ident,project_id)).fetchone()
        if record:
            metadata=json.loads(record["metadata"])
            metadata["workflow_freshness"]="stale";metadata["workflow_stale_reason"]=reason
            db.execute("UPDATE artifacts SET metadata=? WHERE id=?",(encoded(metadata),ident))
    for record in db.execute("SELECT * FROM requirements WHERE project_id=?",(project_id,)):
        if record["status"]=="verified" and artifact_ids&set(json.loads(record["evidence"])):
            db.execute("INSERT INTO study_review_invalidations(project_id,kind,record_id,previous,reason,created_at) VALUES(?,?,?,?,?,?)",
                       (project_id,"requirement",record["id"],encoded(dict(record)),reason,now()))
            db.execute("UPDATE requirements SET status='draft',notes=? WHERE id=?",(record["notes"]+"\n上游变更，需复核："+reason,record["id"]))
    for record in db.execute("SELECT * FROM equipment_records WHERE project_id=?",(project_id,)):
        parameters=json.loads(record["data"])["parameters"]
        affected=any(p.get("calculation_id") in calculation_ids or p.get("artifact_id") in artifact_ids for p in parameters)
        if affected and record["status"]=="reviewed":
            db.execute("INSERT INTO study_review_invalidations(project_id,kind,record_id,previous,reason,created_at) VALUES(?,?,?,?,?,?)",
                       (project_id,"equipment",record["id"],encoded(dict(record)),reason,now()))
            db.execute("UPDATE equipment_records SET status='draft',review_notes=?,updated_at=? WHERE id=?",("上游变更，需复核："+reason,now(),record["id"]))
    return sorted(changed)


def save_parameter(project_id,p):
    try:
        q=units.Quantity(p.value,p.unit);base=q.to_base_units()
        if not math.isfinite(float(base.magnitude)):raise ValueError("非有限参数")
    except Exception as exc:raise ValueError("参数数值或单位无效") from exc
    data=p.model_dump(exclude={"key","expected_revision"})
    data.update(base_value=float(base.magnitude),base_unit=str(base.units))
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        old=db.execute("SELECT * FROM study_parameters WHERE project_id=? AND key=?",(project_id,p.key)).fetchone()
        if (old and p.expected_revision!=old["revision"]) or (not old and p.expected_revision is not None):raise ValueError("参数版本已变化，请重新读取")
        ident=old["id"] if old else uid();revision=old["revision"]+1 if old else 1;stamp=now()
        if old:db.execute("UPDATE study_parameters SET revision=?,data=?,updated_at=? WHERE id=?",(revision,encoded(data),stamp,ident))
        else:db.execute("INSERT INTO study_parameters VALUES(?,?,?,?,?,?,?)",(ident,project_id,p.key,revision,encoded(data),stamp,stamp))
        db.execute("INSERT INTO study_parameter_revisions VALUES(?,?,?,?)",(ident,revision,encoded(data),stamp))
        roots={r["key"] for r in db.execute("SELECT key,config FROM study_tasks WHERE project_id=?",(project_id,))
               if any(b["kind"]=="parameter" and b["key"]==p.key for b in json.loads(r["config"])["bindings"])}
        changed=invalidate(db,project_id,roots,f"参数{p.key}更新至v{revision}") if roots else []
        log(db,project_id,"parameter_saved",{"key":p.key,"revision":revision,"invalidated":changed})
        row=db.execute("SELECT * FROM study_parameters WHERE id=?",(ident,)).fetchone()
    return {"parameter":parameter_row(row),"invalidated_tasks":changed}


def save_task(project_id,p):
    from app.toolbox import SCHEMAS
    allowed=set(SCHEMAS)-{"ask_user","equipment_save","claim_save","evidence_add","study_parameter_save","study_task_save","study_status"}
    if p.tool not in allowed:raise ValueError("任务工具不支持；项目状态修改须通过相应入口执行")
    config=p.model_dump(exclude={"key","expected_revision"})
    encoded(config)
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        records={r["key"]:json.loads(r["config"]) for r in db.execute("SELECT key,config FROM study_tasks WHERE project_id=?",(project_id,))}
        old=db.execute("SELECT * FROM study_tasks WHERE project_id=? AND key=?",(project_id,p.key)).fetchone()
        if (old and p.expected_revision!=old["revision"]) or (not old and p.expected_revision is not None):raise ValueError("任务版本已变化，请重新读取")
        records[p.key]=config
        for key in dependencies(config):
            if key not in records:raise ValueError("上游任务不存在："+key)
        for binding in config["bindings"]:
            pointer(binding["target"]);pointer(binding["path"])
            if binding["kind"]=="parameter":
                if binding["path"]:raise ValueError("参数绑定只取其数值，path须为空")
                if not db.execute("SELECT id FROM study_parameters WHERE project_id=? AND key=?",(project_id,binding["key"])).fetchone():raise ValueError("参数不存在："+binding["key"])
            if binding["unit"]:
                try:units.Unit(binding["unit"])
                except Exception as exc:raise ValueError("绑定单位无效") from exc
        visiting=set();visited=set()
        def visit(key):
            if key in visiting:raise ValueError("任务依赖不能形成环")
            if key in visited:return
            visiting.add(key)
            for dependency in dependencies(records[key]):visit(dependency)
            visiting.remove(key);visited.add(key)
        for key in records:visit(key)
        ident=old["id"] if old else uid();revision=old["revision"]+1 if old else 1;stamp=now()
        if old:db.execute("UPDATE study_tasks SET revision=?,config=?,status='stale',reason='任务定义更新',updated_at=? WHERE id=?",(revision,encoded(config),stamp,ident))
        else:db.execute("INSERT INTO study_tasks VALUES(?,?,?,?,?,?,?,?,?,?)",(ident,project_id,p.key,revision,encoded(config),"pending",None,"",stamp,stamp))
        db.execute("INSERT INTO study_task_revisions VALUES(?,?,?,?)",(ident,revision,encoded(config),stamp))
        changed=invalidate(db,project_id,{p.key},f"任务{p.key}更新至v{revision}") if old else []
        log(db,project_id,"task_saved",{"key":p.key,"revision":revision},task_id=ident)
        row=db.execute("SELECT * FROM study_tasks WHERE id=?",(ident,)).fetchone()
        result=task_row(db,row)
    return {"task":result,"invalidated_tasks":changed}


def resolve(db,project_id,task):
    config=json.loads(task["config"]);arguments=json.loads(encoded(config["arguments"]));sources=[]
    for key in sorted(dependencies(config)):
        parent=db.execute("SELECT * FROM study_tasks WHERE project_id=? AND key=?",(project_id,key)).fetchone()
        if not parent or parent["status"]!="succeeded":raise ValueError("上游任务尚无当前有效结果："+key)
        attempt=db.execute("SELECT * FROM study_attempts WHERE id=?",(parent["latest_attempt_id"],)).fetchone()
        if not attempt or attempt["status"]!="succeeded":raise ValueError("上游任务结果不可用："+key)
        sources.append({"kind":"task","key":key,"revision":parent["revision"],"attempt_id":attempt["id"],
                        "result_sha256":hashlib.sha256(attempt["result"].encode("utf-8")).hexdigest()})
    for binding in config["bindings"]:
        try:
            if binding["kind"]=="parameter":
                row=db.execute("SELECT * FROM study_parameters WHERE project_id=? AND key=?",(project_id,binding["key"])).fetchone()
                data=json.loads(row["data"])
                value=data["value"]
                if binding["unit"]:value=float(units.Quantity(value,data["unit"]).to(binding["unit"]).magnitude)
                sources.append({"kind":"parameter","key":binding["key"],"revision":row["revision"],"data":data,"target":binding["target"],"target_unit":binding["unit"] or data["unit"]})
            else:
                parent=db.execute("SELECT latest_attempt_id FROM study_tasks WHERE project_id=? AND key=?",(project_id,binding["key"])).fetchone()
                row=db.execute("SELECT result FROM study_attempts WHERE id=?",(parent["latest_attempt_id"],)).fetchone()
                value=at(json.loads(row["result"]),binding["path"])
            if binding.get("render_as")=="text":
                value=binding.get("prefix","")+(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))+binding.get("suffix","")
            put(arguments,binding["target"],value)
        except Exception as exc:raise ValueError("输入绑定无法解析："+binding["target"]+"；"+str(exc)) from exc
    return arguments,sources


def sources_current(db,project_id,sources):
    for source in sources:
        if source["kind"]=="parameter":
            row=db.execute("SELECT revision FROM study_parameters WHERE project_id=? AND key=?",(project_id,source["key"])).fetchone()
            if not row or row["revision"]!=source["revision"]:return False
        else:
            row=db.execute("SELECT revision,status,latest_attempt_id FROM study_tasks WHERE project_id=? AND key=?",(project_id,source["key"])).fetchone()
            if not row or row["revision"]!=source["revision"] or row["status"]!="succeeded" or row["latest_attempt_id"]!=source["attempt_id"]:return False
    return True


def start_execution(project_id,p):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        require_reconciled(db,project_id)
        if db.execute("SELECT id FROM study_executions WHERE project_id=? AND status IN ('queued','running')",(project_id,)).fetchone() or db.execute("SELECT a.id FROM study_attempts a JOIN study_tasks t ON t.id=a.task_id WHERE t.project_id=? AND a.status='running'",(project_id,)).fetchone():raise ValueError("项目已有研究任务执行中或正在等待取消的调用返回")
        records={r["key"]:dict(r) for r in db.execute("SELECT * FROM study_tasks WHERE project_id=?",(project_id,))}
        order=[];seen=set()
        def visit(key):
            if key in seen:return
            if key not in records:raise ValueError("任务不存在："+key)
            seen.add(key)
            for parent in sorted(dependencies(json.loads(records[key]["config"]))):visit(parent)
            order.append(key)
        for key in p.task_keys:visit(key)
        ident=uid();stamp=now();state={"order":order,"completed":[],"tool_calls":0,"active_seconds":0.}
        db.execute("INSERT INTO study_executions VALUES(?,?,?,?,?,?,?)",(ident,project_id,p.model_dump_json(),"queued",encoded(state),stamp,stamp))
        log(db,project_id,"execution_queued",state,execution_id=ident)
    return execution(ident)


def execution(ident):
    row=one("SELECT * FROM study_executions WHERE id=?",(ident,))
    if not row:raise ValueError("研究执行不存在")
    for k in ["request","state"]:row[k]=json.loads(row[k])
    row["events"]=rows("SELECT * FROM study_events WHERE execution_id=? ORDER BY id",(ident,))
    for event in row["events"]:event["payload"]=json.loads(event["payload"])
    return row


def cancel_execution(ident):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT project_id,status FROM study_executions WHERE id=?",(ident,)).fetchone()
        if not row:raise ValueError("研究执行不存在")
        if row["status"] in {"queued","running"}:
            db.execute("UPDATE study_executions SET status='cancelled',updated_at=? WHERE id=?",(now(),ident))
            log(db,row["project_id"],"cancel_requested",{},execution_id=ident)
    return execution(ident)


def resume_execution(ident):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM study_executions WHERE id=?",(ident,)).fetchone()
        if not row or row["status"] not in {"interrupted","failed","blocked","cancelled"}:raise ValueError("当前执行状态不允许恢复；预算用尽需新建执行")
        require_reconciled(db,row["project_id"])
        if db.execute("SELECT id FROM study_executions WHERE project_id=? AND status IN ('queued','running')",(row["project_id"],)).fetchone() or db.execute("SELECT a.id FROM study_attempts a JOIN study_tasks t ON t.id=a.task_id WHERE t.project_id=? AND a.status='running'",(row["project_id"],)).fetchone():raise ValueError("项目已有研究任务执行中或正在等待取消的调用返回")
        # Reconcile all dependencies, including previously successful checkpoints.
        state=json.loads(row["state"]);state["completed"]=[]
        db.execute("UPDATE study_executions SET status='queued',state=?,updated_at=? WHERE id=?",(encoded(state),now(),ident))
        log(db,row["project_id"],"execution_resumed",{},execution_id=ident)
    return execution(ident)


def require_reconciled(db,project_id):
    if db.execute("SELECT a.id FROM study_attempts a JOIN study_tasks t ON t.id=a.task_id WHERE t.project_id=? AND a.status='interrupted'",(project_id,)).fetchone():
        raise ValueError("存在结果未知的中断调用，请先核对并确认处置，不能直接重放")


def reconcile_attempt(ident,p):
    """Record an explicit decision before retrying or adopting an uncertain call."""
    from app.toolbox import SCHEMAS
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        attempt=db.execute("SELECT * FROM study_attempts WHERE id=?",(ident,)).fetchone()
        if not attempt or attempt["status"]!="interrupted":raise ValueError("该调用不处于结果未知状态或已处理")
        task=db.execute("SELECT * FROM study_tasks WHERE id=?",(attempt["task_id"],)).fetchone()
        project_id=task["project_id"]
        if db.execute("SELECT id FROM study_executions WHERE project_id=? AND status IN ('queued','running')",(project_id,)).fetchone():
            raise ValueError("项目仍有执行中的研究任务，请先等待或取消")
        result={};calculation_id=None
        if p.decision=="adopt":
            if task["revision"]!=attempt["task_revision"] or task["latest_attempt_id"]!=ident or not sources_current(db,project_id,json.loads(attempt["sources"])):
                raise ValueError("输入版本或上游已变化，旧结果不能提升为当前结果；可确认重新执行")
            config=json.loads(task["config"])
            record=db.execute("SELECT * FROM calculations WHERE id=? AND project_id=?",(p.calculation_id,project_id)).fetchone()
            if not record or record["tool"]!=config["tool"] or record["created_at"]<attempt["started_at"]:
                raise ValueError("计算记录不属于本项目的当前中断工具调用")
            expected=SCHEMAS[config["tool"]].model_validate_json(attempt["input"]).model_dump()
            if encoded(expected)!=encoded(json.loads(record["input"])):
                raise ValueError("计算记录的实际输入与中断调用不一致")
            result=json.loads(record["result"])
            if "error" in result or result.get("status")=="unavailable" or result.get("validation",{}).get("passed") is False:
                raise ValueError("失败或不可用的计算记录不能采用")
            calculation_id=record["id"];result={"calculation_id":calculation_id,**result}
        status="succeeded" if p.decision=="adopt" else "retry_approved"
        db.execute("UPDATE study_attempts SET status=?,calculation_id=?,result=?,error=?,finished_at=? WHERE id=?",
                   (status,calculation_id,encoded(result),"中断后人工确认："+p.notes,now(),ident))
        if task["latest_attempt_id"]==ident:
            db.execute("UPDATE study_tasks SET status=?,reason=?,updated_at=? WHERE id=?",
                       ("succeeded" if p.decision=="adopt" else "stale","中断后已核对："+p.notes,now(),task["id"]))
        for artifact in result.get("artifacts",[]):
            item=db.execute("SELECT metadata FROM artifacts WHERE id=? AND project_id=?",(artifact["id"],project_id)).fetchone()
            if not item:raise ValueError("计算记录关联文件不存在，不能采用")
            metadata=json.loads(item["metadata"])
            metadata.update(workflow_task=task["key"],workflow_attempt_id=ident,workflow_freshness="current")
            db.execute("UPDATE artifacts SET metadata=? WHERE id=?",(encoded(metadata),artifact["id"]))
        log(db,project_id,"attempt_reconciled",{"attempt_id":ident,**p.model_dump()},attempt["execution_id"],task["id"])
    return {"attempt_id":ident,"status":status,"decision":p.decision,"note":"处置确认只关闭执行结果未知状态，不构成科学或工程审核。"}


def recover_interrupted():
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT * FROM study_attempts WHERE status='running'").fetchall():
            db.execute("UPDATE study_attempts SET status='interrupted',error='服务中断；副作用是否完成需复核',finished_at=? WHERE id=?",(now(),row["id"]))
            db.execute("UPDATE study_tasks SET status='interrupted',reason='服务中断；已生成文件须复核',updated_at=? WHERE id=? AND latest_attempt_id=?",(now(),row["task_id"],row["id"]))
            project_id=db.execute("SELECT project_id FROM study_tasks WHERE id=?",(row["task_id"],)).fetchone()["project_id"]
            log(db,project_id,"task_interrupted",{"attempt_id":row["id"],"outcome":"unknown","automatic_replay":False},row["execution_id"],row["task_id"])
        db.execute("UPDATE study_executions SET status='interrupted',updated_at=? WHERE status IN ('queued','running')",(now(),))


def run_execution(ident):
    from app.toolbox import run_tool, UncertainToolError
    started=time.monotonic()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM study_executions WHERE id=?",(ident,)).fetchone()
        if not row or row["status"]!="queued":return
        project_id=row["project_id"];p=WorkflowStart.model_validate_json(row["request"]);state=json.loads(row["state"])
        db.execute("UPDATE study_executions SET status='running',updated_at=? WHERE id=?",(now(),ident))
    base_seconds=state.get("active_seconds",0.)
    def finish(status,reason=""):
        state["active_seconds"]=base_seconds+time.monotonic()-started;state["reason"]=reason
        with connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current=db.execute("SELECT status FROM study_executions WHERE id=?",(ident,)).fetchone()["status"]
            if current=="cancelled":status="cancelled"
            if status=="completed" and not all(db.execute("SELECT status FROM study_tasks WHERE project_id=? AND key=?",(project_id,key)).fetchone()["status"]=="succeeded" for key in state["order"]):
                status="blocked";reason="执行期间上游变化，需重新确认并恢复";state["reason"]=reason
            db.execute("UPDATE study_executions SET status=?,state=?,updated_at=? WHERE id=?",(status,encoded(state),now(),ident))
            log(db,project_id,"execution_"+status,{"reason":reason},execution_id=ident)
    try:
        for key in state["order"]:
            if one("SELECT status FROM study_executions WHERE id=?",(ident,))["status"]=="cancelled":return finish("cancelled")
            with connection() as db:
                db.execute("BEGIN IMMEDIATE")
                task=db.execute("SELECT * FROM study_tasks WHERE project_id=? AND key=?",(project_id,key)).fetchone()
                require_reconciled(db,project_id)
                if task["status"]=="succeeded":
                    state["completed"].append(key)
                    continue
                if state["tool_calls"]>=p.max_tools or base_seconds+time.monotonic()-started>=p.max_seconds:
                    budget=True
                else:budget=False
                if not budget:
                    arguments,sources=resolve(db,project_id,task)
                    config=json.loads(task["config"]);attempt_id=uid()
                    # Re-running any upstream task makes all previous descendants stale.
                    invalidate(db,project_id,{key},"任务"+key+"开始新一次执行")
                    db.execute("INSERT INTO study_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(attempt_id,task["id"],ident,task["revision"],encoded(arguments),encoded(sources),"running",None,"{}","",now(),None))
                    db.execute("UPDATE study_tasks SET status='running',latest_attempt_id=?,reason='',updated_at=? WHERE id=?",(attempt_id,now(),task["id"]))
                    state["tool_calls"]+=1
                    state["active_seconds"]=base_seconds+time.monotonic()-started
                    db.execute("UPDATE study_executions SET state=?,updated_at=? WHERE id=?",(encoded(state),now(),ident))
                    log(db,project_id,"task_started",{"key":key,"attempt_id":attempt_id,"arguments":arguments,"sources":sources},ident,task["id"])
            if budget:return finish("budget_exhausted","达到工具次数或累计执行时间限制")
            result={};error="";uncertain=False
            try:
                result=run_tool(project_id,config["tool"],arguments)
                if "error" in result or result.get("status")=="unavailable" or result.get("validation",{}).get("passed") is False:
                    error="工具未通过："+str(result.get("error") or result.get("validation") or result.get("status"))[:1500]
            except UncertainToolError as exc:error=str(exc)[:1800];uncertain=True
            except Exception as exc:error=str(exc)[:1800]
            with connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current=db.execute("SELECT revision,latest_attempt_id FROM study_tasks WHERE id=?",(task["id"],)).fetchone()
                cancelled=db.execute("SELECT status FROM study_executions WHERE id=?",(ident,)).fetchone()["status"]=="cancelled"
                stale=current["revision"]!=task["revision"] or current["latest_attempt_id"]!=attempt_id or not sources_current(db,project_id,sources)
                status="interrupted" if uncertain else "failed" if error else "cancelled" if cancelled else "stale" if stale else "succeeded"
                reason=error or ("执行期间输入或依赖变化" if stale else "取消后保留计算结果，未提升为当前结果" if cancelled else "")
                db.execute("UPDATE study_attempts SET status=?,calculation_id=?,result=?,error=?,finished_at=? WHERE id=?",(status,result.get("calculation_id"),encoded(result),reason,now(),attempt_id))
                db.execute("UPDATE study_tasks SET status=?,reason=?,updated_at=? WHERE id=? AND latest_attempt_id=?",(status,reason,now(),task["id"],attempt_id))
                for artifact in result.get("artifacts",[]):
                    item=db.execute("SELECT metadata FROM artifacts WHERE id=?",(artifact["id"],)).fetchone()
                    metadata=json.loads(item["metadata"]);metadata.update(workflow_task=key,workflow_attempt_id=attempt_id,workflow_freshness="current" if status=="succeeded" else "stale")
                    db.execute("UPDATE artifacts SET metadata=? WHERE id=?",(encoded(metadata),artifact["id"]))
                log(db,project_id,"task_"+status,{"key":key,"attempt_id":attempt_id,"calculation_id":result.get("calculation_id"),"reason":reason},ident,task["id"])
                if status=="succeeded":state["completed"].append(key)
                state["active_seconds"]=base_seconds+time.monotonic()-started
                db.execute("UPDATE study_executions SET state=?,updated_at=? WHERE id=?",(encoded(state),now(),ident))
            if status!="succeeded":return finish("interrupted" if uncertain else "cancelled" if cancelled else "failed" if error else "blocked",reason)
        # Updates can happen between checkpoints: never finish with a stale dependency.
        with connection() as db:
            valid=all(db.execute("SELECT status FROM study_tasks WHERE project_id=? AND key=?",(project_id,key)).fetchone()["status"]=="succeeded" for key in state["order"])
        finish("completed" if valid else "blocked","" if valid else "执行期间上游变化，需重新确认并恢复")
    except Exception as exc:finish("blocked",str(exc)[:1800])


_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix="chemellm-study")
_active=set();_guard=threading.Lock()


def dispatch_execution(ident):
    with _guard:
        if ident in _active:return
        _active.add(ident)
    def worker():
        try:run_execution(ident)
        finally:
            with _guard:_active.discard(ident)
            row=one("SELECT status FROM study_executions WHERE id=?",(ident,))
            if row and row["status"]=="queued":dispatch_execution(ident)
    _pool.submit(worker)


def study_status(project_id):
    with connection() as db:
        parameters=[parameter_row(r) for r in db.execute("SELECT * FROM study_parameters WHERE project_id=? ORDER BY key",(project_id,))]
        tasks=[task_row(db,r) for r in db.execute("SELECT * FROM study_tasks WHERE project_id=? ORDER BY created_at,key",(project_id,))]
        uncertain=[]
        for row in db.execute("SELECT a.*,t.key FROM study_attempts a JOIN study_tasks t ON t.id=a.task_id WHERE t.project_id=? AND a.status='interrupted' ORDER BY a.started_at",(project_id,)):
            item=attempt_row(row)
            item["candidate_calculations"]=[{**dict(c),"input":json.loads(c["input"]),"result":json.loads(c["result"])} for c in db.execute(
                "SELECT * FROM calculations WHERE project_id=? AND created_at>=? ORDER BY created_at DESC LIMIT 20",(project_id,row["started_at"]))]
            uncertain.append(item)
    executions=rows("SELECT * FROM study_executions WHERE project_id=? ORDER BY created_at DESC LIMIT 30",(project_id,))
    for r in executions:
        for k in ["request","state"]:r[k]=json.loads(r[k])
    return {"parameters":parameters,"tasks":tasks,"executions":executions,"uncertain_attempts":uncertain,
            "scope":"仅显式登记的参数和依赖参与失效传播；旧文件和历史不会删除。执行完成不代表科学或工程审核。"}


def study_export(project_id):
    result=study_status(project_id)
    for table in ["study_parameter_revisions","study_task_revisions","study_attempts"]:
        if table=="study_parameter_revisions":query="SELECT r.* FROM study_parameter_revisions r JOIN study_parameters p ON p.id=r.parameter_id WHERE p.project_id=?"
        else:query=f"SELECT r.* FROM {table} r JOIN study_tasks t ON t.id=r.task_id WHERE t.project_id=?"
        values=rows(query,(project_id,))
        for value in values:
            for k in ["data","config","input","sources","result"]:
                if k in value:value[k]=json.loads(value[k])
        result[table]=values
    result["events"]=rows("SELECT * FROM study_events WHERE project_id=? ORDER BY id",(project_id,))
    result["review_invalidations"]=rows("SELECT * FROM study_review_invalidations WHERE project_id=? ORDER BY id",(project_id,))
    for event in result["events"]:event["payload"]=json.loads(event["payload"])
    for r in result["review_invalidations"]:r["previous"]=json.loads(r["previous"])
    return result
