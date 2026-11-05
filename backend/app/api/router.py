import asyncio,json
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import StreamingResponse
from app.api.schemas import ChatRequest,MockCaseRequest,ApprovalDecision,BadCaseCreate
from app.core.config import settings
from app.core.security import SecurityContext,get_security_context
from app.db.database import fetch_all,fetch_one,execute,loads,dumps
from app.fixtures.mock_data import MOCK_CASES
from app.llm.provider import describe_provider
from app.rag.hybrid import retrieve
from app.services.router import route_intent
from app.services.tools import get_order,get_logistics,create_ticket,get_customer
from app.services.tasking import create_task,get_task,list_events
router=APIRouter(prefix='/api/v1')

@router.get('/runtime')
def runtime():
    try: import langgraph; lg=True
    except Exception: lg=False
    return {
        'mock_mode':settings.mock_mode,
        **describe_provider(),
        'langgraph_available':lg,
        'database':settings.database_path,
    }
@router.get('/mock-cases')
def mock_cases(): return MOCK_CASES
@router.post('/mock-cases/run')
def run_mock(body:MockCaseRequest):
    c=next((x for x in MOCK_CASES if x['case_key']==body.case_key),None)
    if not c: raise HTTPException(404,'mock case not found')
    ctx=SecurityContext(c['user_id'],'TENANT-DEMO','customer'); return _chat(ChatRequest(message=c['message']),ctx)
@router.post('/chat')
def chat(body:ChatRequest,ctx:SecurityContext=Depends(get_security_context)): return _chat(body,ctx)
def _chat(body,ctx):
    r=route_intent(body.message); intent=r['intent']; oid=r.get('order_id')
    if intent=='faq':
        docs=retrieve(body.message,top_n=3); answer=docs[0]['content'] if docs else '当前知识库没有足够依据。'; return {'mode':'direct_rag','intent':intent,'answer':answer,'citations':[{'title':x['title'],'version':x['version']} for x in docs]}
    if intent=='chat': return {'mode':'chat','intent':intent,'answer':'你好，我可以帮你查询订单、物流、退款政策或创建售后工单。'}
    if not oid: raise HTTPException(400,'请提供订单号，例如 O10001')
    try:
        if intent=='order_query': return {'mode':'direct_tool','intent':intent,'order':get_order(oid,ctx)}
        if intent=='logistics_query':
            log=get_logistics(oid,ctx)
            if not log:return {'mode':'direct_tool','intent':intent,'answer':'该订单暂无物流信息。'}
            ticket=None
            if int(log['last_update_hours'])>=24: ticket=create_ticket(ctx,oid,'logistics_abnormal',body.message,stale_hours=int(log['last_update_hours']))
            return {'mode':'direct_tool','intent':intent,'logistics':log,'ticket':ticket,'answer':f"当前物流状态 {log['status']}，最后节点：{log['last_event']}。"+(f" 已创建工单 {ticket['id']}。" if ticket else '')}
    except PermissionError as e: raise HTTPException(403,str(e))
    except ValueError as e: raise HTTPException(404,str(e))
    return {'mode':'workflow','intent':intent,**create_task(ctx,intent,{'message':body.message,'order_id':oid,'role':ctx.role})}

@router.get('/tasks/{task_id}')
def task(task_id):
    x=get_task(task_id)
    if not x: raise HTTPException(404,'task not found')
    return x
@router.get('/tasks/{task_id}/events')
async def events(task_id):
    async def stream():
        last=0; idle=0
        while True:
            es=list_events(task_id,last)
            for e in es:
                last=e['id']; yield f"data: {json.dumps({'id':e['id'],'event_type':e['event_type'],'created_at':e['created_at'],**e['payload']},ensure_ascii=False)}\n\n"
            t=get_task(task_id)
            if t and t['status'] in {'completed','failed'} and not es: break
            idle+=1
            if idle>240: break
            await asyncio.sleep(.5)
    return StreamingResponse(stream(),media_type='text/event-stream')
@router.get('/tickets')
def tickets(): return fetch_all('SELECT * FROM tickets ORDER BY created_at DESC')
@router.get('/refunds')
def refunds(): return fetch_all('SELECT * FROM refund_requests ORDER BY created_at DESC')
@router.get('/approvals')
def approvals(): return fetch_all('SELECT * FROM approval_records ORDER BY created_at DESC,sequence_no')
@router.post('/approvals/{approval_id}/decision')
def approve(approval_id,body:ApprovalDecision):
    if body.status not in {'approved','rejected'}: raise HTTPException(400,'status must approved/rejected')
    a=fetch_one('SELECT * FROM approval_records WHERE id=?',(approval_id,))
    if not a: raise HTTPException(404,'approval not found')
    prior=fetch_one("SELECT * FROM approval_records WHERE task_id=? AND sequence_no<? AND status='pending' ORDER BY sequence_no LIMIT 1",(a['task_id'],a['sequence_no']))
    if prior: raise HTTPException(409,f"请先完成前置审批：{prior['approver_role']}")
    execute('UPDATE approval_records SET status=?,approver_user_id=?,comment=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(body.status,body.approver_user_id,body.comment,approval_id))
    if body.status=='rejected': execute("UPDATE approval_records SET status='cancelled' WHERE task_id=? AND status='pending' AND id<>?",(a['task_id'],approval_id))
    allrows=fetch_all('SELECT * FROM approval_records WHERE task_id=?',(a['task_id'],))
    if any(r['status']=='rejected' for r in allrows) or not any(r['status']=='pending' for r in allrows): execute("UPDATE tasks SET status='resume_pending',current_step='approval_resume',updated_at=CURRENT_TIMESTAMP WHERE id=?",(a['task_id'],))
    return {'approval':fetch_one('SELECT * FROM approval_records WHERE id=?',(approval_id,)),'all':allrows}
@router.get('/knowledge')
def knowledge():
    rows=fetch_all('SELECT * FROM knowledge ORDER BY category,effective_from DESC')
    for r in rows:r['keywords']=loads(r.pop('keywords_json','[]'),[])
    return rows
@router.get('/traces')
def traces(limit:int=300):
    rows=fetch_all('SELECT * FROM trace_steps ORDER BY id DESC LIMIT ?',(min(limit,1000),))
    for r in rows:r['input']=loads(r.pop('input_json','{}'),{});r['output']=loads(r.pop('output_json','{}'),{})
    return rows
@router.get('/dashboard')
def dashboard():
    return {'tickets_total':fetch_one('SELECT COUNT(*) c FROM tickets')['c'],'open_tickets':fetch_one("SELECT COUNT(*) c FROM tickets WHERE status='open'")['c'],'refund_total':float(fetch_one('SELECT COALESCE(SUM(amount),0) v FROM refund_requests')['v'] or 0),'waiting_approval':fetch_one("SELECT COUNT(*) c FROM tasks WHERE status='waiting_approval'")['c'],'ticket_by_priority':fetch_all('SELECT priority,COUNT(*) count FROM tickets GROUP BY priority'),'ticket_by_category':fetch_all('SELECT category,COUNT(*) count FROM tickets GROUP BY category')}
@router.get('/evaluation')
def evaluation():
    from app.eval.run_eval import evaluate
    return {'cases':evaluate()}
@router.get('/bad-cases')
def bad_cases():
    rows=fetch_all('SELECT * FROM bad_cases ORDER BY id DESC')
    for r in rows:r['expected_result']=loads(r.pop('expected_result_json','{}'),{});r['fixed']=bool(r['fixed'])
    return rows
@router.post('/bad-cases')
def bad(body:BadCaseCreate): execute('INSERT INTO bad_cases(trace_id,query,error_type,root_cause,expected_result_json) VALUES(?,?,?,?,?)',(body.trace_id,body.query,body.error_type,body.root_cause,dumps(body.expected_result))); return {'status':'created'}
