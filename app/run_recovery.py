"""Durable conversation tool attempts and explicit uncertain-outcome decisions."""
import hashlib
import json
from typing import Literal
from pydantic import Field, model_validator
from app.schemas import Inputs
from app.db import connection, now, uid
from app.toolbox import SCHEMAS


def encoded(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True)


def input_hash(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def failed_result(result):
    return 'error' in result or result.get('status')=='unavailable' or result.get('validation',{}).get('passed') is False


def tool_message(state,call_id,result):
    content=encoded(result)
    if len(content)>30000:
        full_sha256=hashlib.sha256(content.encode()).hexdigest()
        # Never feed a misleading JSON prefix back as if it were a valid result.
        # Keep actionable diagnostics and artifact references, and direct the model
        # to the project-scoped calculation_read tool for complete values.
        compact={key:result[key] for key in ('calculation_id','tool','status','type',
            'execution_failure_id','task_key') if key in result}
        if isinstance(result.get('error'),str):
            compact['error']=result['error'][:8000]
        if 'validation' in result:
            validation=encoded(result['validation'])
            compact['validation']=result['validation'] if len(validation)<=10000 else {
                'notice':'完整校验明细在执行记录内','sha256':hashlib.sha256(validation.encode()).hexdigest(),
                'outline':{'type':type(result['validation']).__name__,
                           'keys':list(result['validation'])[:40] if isinstance(result['validation'],dict) else None}}
        if isinstance(result.get('artifacts'),list):
            compact['artifacts']=[{k:item[k] for k in ('id','name','kind','download_url','preview_url','has_visual_preview') if k in item}
                                  for item in result['artifacts'] if isinstance(item,dict)]
        for key in ('warnings','issues','errors','blockers'):
            value=result.get(key)
            if isinstance(value,list):
                compact[key]=value[:20]
                if len(value)>20:compact[key+'_total']=len(value)
        compact['result_outline']={key:{'type':type(value).__name__,
            **({'items':len(value)} if isinstance(value,(list,dict,str)) else {}),
            **({'keys':[str(k)[:100] for k in list(value)[:20]]} if isinstance(value,dict) else {})}
            for key,value in result.items() if key not in compact}
        compact['result_sha256']=full_sha256
        compact['notice']='完整结果保存在对应执行记录/计算记录中；用calculation_read按JSON Pointer或分页读取，消息中没有截断替代值。'
        content=encoded(compact)
        if len(content)>30000:
            content=encoded({'calculation_id':result.get('calculation_id'),'tool':result.get('tool'),
                'status':result.get('status'),'execution_failure_id':result.get('execution_failure_id'),
                'result_sha256':full_sha256,
                'notice':'工具结果过长；完整值保留在持久执行记录中，请用calculation_read按路径读取。'})
    state['messages'].append({'role':'tool','tool_call_id':call_id,'content':content})
    state['pending_calls']=[c for c in state.get('pending_calls',[]) if c['id']!=call_id]


def record_result(state,attempt,result,failed):
    encoded(result)  # Serialization must succeed before changing the attempt outcome.
    if failed:
        failure={'id':uid(),'call_id':attempt['call_id'],'tool':attempt['tool'],'task_key':attempt['task_key'],
                 'input':attempt['input'],'input_sha256':attempt.get('input_sha256'),
                 'error':str(result.get('error') or result.get('validation') or result.get('status'))[:1800],'resolved_by':None}
        state['failures'].append(failure)
        result={**result,'execution_failure_id':failure['id'],'task_key':attempt['task_key']}
    else:
        for failure in state['failures']:
            if not failure.get('resolved_by') and failure['tool']==attempt['tool'] and failure.get('task_key','')==attempt['task_key'] and (
                    failure['id'] in attempt['repairs'] or attempt.get('input_sha256') is not None and failure.get('input_sha256')==attempt['input_sha256']):
                failure['resolved_by']={'call_id':attempt['call_id'],'calculation_id':result.get('calculation_id'),'input_sha256':attempt['input_sha256']}
    state['unresolved_tools']=sorted({f['tool'] for f in state['failures'] if not f.get('resolved_by')})
    attempt.update(status='failed' if failed else 'succeeded',finished_at=now(),calculation_id=result.get('calculation_id'))
    tool_message(state,attempt['call_id'],result)
    return result


def require_reconciled(db,project_id):
    for row in db.execute('SELECT result FROM runs WHERE project_id=?',(project_id,)):
        if any(a['status'] in {'running','unknown'} for a in json.loads(row['result']).get('tool_attempts',[])):
            raise ValueError('存在结果未知或尚未返回的对话工具调用，请先核对并确认处置')


class CallResolution(Inputs):
    decision:Literal['retry','adopt']
    reviewer:str=Field(min_length=2,max_length=100)
    notes:str=Field(min_length=10,max_length=3000)
    calculation_id:str|None=Field(default=None,max_length=100)

    @model_validator(mode='after')
    def selection(self):
        if self.decision=='adopt' and not self.calculation_id:raise ValueError('采用已有结果须选择计算记录')
        if self.decision=='retry' and self.calculation_id:raise ValueError('确认重试不能同时采用旧结果')
        return self


def unknown_calls(ident):
    with connection() as db:
        row=db.execute('SELECT project_id,result FROM runs WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('任务不存在')
        result=[]
        for attempt in json.loads(row['result']).get('tool_attempts',[]):
            if attempt['status']!='unknown':continue
            candidates=[]
            for record in db.execute('SELECT id,tool,input,result,created_at FROM calculations WHERE project_id=? AND tool=? AND created_at>=? ORDER BY created_at DESC LIMIT 20',
                                     (row['project_id'],attempt['tool'],attempt['started_at'])):
                value=dict(record)
                value['input']=json.loads(value['input']);value['result']=json.loads(value['result'])
                candidates.append(value)
            result.append({**attempt,'candidate_calculations':candidates})
        return result


def reconcile_call(ident,attempt_id,p):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM runs WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('任务不存在')
        if row['status'] in {'queued','running'}:raise ValueError('任务仍在运行，请等待工具返回')
        state=json.loads(row['result'])
        state.setdefault('failures',[]);state.setdefault('unresolved_tools',[])
        attempt=next((a for a in state.get('tool_attempts',[]) if a['id']==attempt_id),None)
        if not attempt or attempt['status']!='unknown':raise ValueError('该调用不处于结果未知状态或已处理')
        if not any(c['id']==attempt['call_id'] for c in state.get('pending_calls',[])):
            raise ValueError('缺少待处理调用上下文，不能直接恢复')
        if p.decision=='adopt':
            record=db.execute('SELECT * FROM calculations WHERE id=? AND project_id=?',(p.calculation_id,row['project_id'])).fetchone()
            if not record or record['tool']!=attempt['tool'] or record['created_at']<attempt['started_at']:
                raise ValueError('计算记录不属于本项目的当前中断工具调用')
            expected=SCHEMAS[attempt['tool']].model_validate(attempt['input']).model_dump()
            if encoded(expected)!=encoded(json.loads(record['input'])):raise ValueError('计算实际输入与中断调用不一致')
            result=json.loads(record['result'])
            if failed_result(result):raise ValueError('失败或不可用的计算记录不能采用')
            for artifact in result.get('artifacts',[]):
                item=db.execute('SELECT metadata,path FROM artifacts WHERE id=? AND project_id=?',(artifact['id'],row['project_id'])).fetchone()
                if not item:raise ValueError('计算关联文件不存在')
                from pathlib import Path
                from app.config import ARTIFACTS
                path=Path(item['path']).resolve()
                if not path.is_relative_to(ARTIFACTS) or not path.is_file():raise ValueError('计算关联文件缺失')
                if json.loads(item['metadata']).get('workflow_freshness')=='stale':raise ValueError('计算关联文件已过期，不能采用')
            record_result(state,attempt,{'calculation_id':record['id'],**result,'execution_reconciliation':{
                'original_outcome':'unknown','decision':'adopt','attempt_id':attempt_id,'note':'服务中断后采用已有记录，本次未重复执行该工具。'}},False)
            attempt['status']='adopted'
        else:
            attempt['status']='retry_approved'
        attempt['resolution']={**p.model_dump(),'at':now()}
        db.execute('UPDATE runs SET result=?,updated_at=? WHERE id=?',(encoded(state),now(),ident))
        db.execute('INSERT INTO events(run_id,type,payload,created_at) VALUES(?,?,?,?)',
                   (ident,'tool_reconciled',encoded({'attempt_id':attempt_id,'call_id':attempt['call_id'],**p.model_dump(),'note':'执行处置，不构成科学或工程审核'}),now()))
    return {'id':ident,'attempt_id':attempt_id,'decision':p.decision,'status':attempt['status']}


def recover_runs():
    """At startup, preserve completed replies and queued siblings; never replay unknowns."""
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('SELECT * FROM runs').fetchall():
            state=json.loads(row['result'])
            recoverable=row['status'] in {'queued','running','interrupted'}
            # Upgrade old interrupted records conservatively, including a lost response
            # whose only durable record was tool_start. Its outcome must be checked.
            if recoverable and 'tool_attempts' not in state:
                state.setdefault('messages',[]);state['tool_attempts']=[]
                answered={m.get('tool_call_id') for m in state['messages'] if m.get('role')=='tool'}
                pending=[c for m in state['messages'] for c in m.get('tool_calls',[]) if c['id'] not in answered]
                unmatched=None
                for event in db.execute('SELECT type,payload,created_at FROM events WHERE run_id=? ORDER BY id',(row['id'],)):
                    if event['type']=='tool_start':unmatched=event
                    elif event['type']=='tool_result':unmatched=None
                if unmatched and not pending:
                    payload=json.loads(unmatched['payload'])
                    call={'id':'legacy-'+uid(),'type':'function','function':{'name':payload['name'],'arguments':encoded(payload['arguments'])}}
                    state['messages'].append({'role':'assistant','content':None,'tool_calls':[call]});pending=[call]
                state['pending_calls']=pending
                for call in pending:
                    try:
                        arguments=json.loads(call['function']['arguments']);metadata=arguments.pop('_execution',{})
                    except (ValueError,TypeError,AttributeError):arguments={};metadata={}
                    state['tool_attempts'].append({'id':uid(),'call_id':call['id'],'tool':call['function']['name'],
                        'input':arguments,'task_key':metadata.get('task_key','') if isinstance(metadata,dict) else '',
                        'repairs':[],'input_sha256':input_hash(arguments),'status':'running',
                        'started_at':unmatched['created_at'] if unmatched else row['updated_at'],'legacy':True})
            unknown=False
            for attempt in state.get('tool_attempts',[]):
                if attempt['status']=='running':
                    from datetime import datetime
                    duration=max(0.,(datetime.fromisoformat(now())-datetime.fromisoformat(attempt['started_at'])).total_seconds())
                    state['active_seconds']=max(state.get('active_seconds',0.),attempt.get('active_seconds_before',state.get('active_seconds',0.))+duration)
                    attempt['recovery_elapsed_upper_bound_seconds']=duration
                    attempt.update(status='unknown',finished_at=now(),reason='服务中断；工具是否已生成成果需核对，未自动重放')
                    db.execute('INSERT INTO events(run_id,type,payload,created_at) VALUES(?,?,?,?)',
                               (row['id'],'tool_interrupted',encoded({'attempt_id':attempt['id'],'call_id':attempt['call_id'],'outcome':'unknown','automatic_replay':False}),now()))
                    unknown=True
            if row['status'] in {'queued','running'} or unknown:
                db.execute("UPDATE runs SET status='interrupted',result=?,updated_at=? WHERE id=?",(encoded(state),now(),row['id']))
            elif recoverable:
                db.execute('UPDATE runs SET result=? WHERE id=?',(encoded(state),row['id']))
