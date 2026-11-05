import time
from uuid import uuid4
from app.db.database import execute,fetch_one,fetch_all,dumps,loads

def new_id(prefix): return f'{prefix}_{uuid4().hex[:16]}'
def create_task(ctx,intent,payload):
    tid=new_id('task'); trace=new_id('trace')
    execute('INSERT INTO tasks(id,trace_id,tenant_id,user_id,intent,status,current_step,payload_json,result_json) VALUES(?,?,?,?,?,?,?,?,?)',(tid,trace,ctx.tenant_id,ctx.user_id,intent,'pending','queued',dumps(payload),'{}'))
    emit_event(tid,'task_created',{'trace_id':trace,'intent':intent}); return {'task_id':tid,'trace_id':trace,'status':'pending'}
def get_task(tid):
    r=fetch_one('SELECT * FROM tasks WHERE id=?',(tid,));
    if not r:return None
    r['payload']=loads(r.pop('payload_json','{}'),{}); r['result']=loads(r.pop('result_json','{}'),{}); return r
def update_task(tid,status=None,current_step=None,result=None,error=None):
    r=fetch_one('SELECT * FROM tasks WHERE id=?',(tid,));
    if not r:return
    execute('UPDATE tasks SET status=?,current_step=?,result_json=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status or r['status'],current_step or r['current_step'],dumps(result) if result is not None else r['result_json'],error,tid))
def claim_next_task():
    r=fetch_one("SELECT id FROM tasks WHERE status IN ('pending','resume_pending') ORDER BY created_at LIMIT 1")
    if not r:return None
    execute("UPDATE tasks SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?",(r['id'],)); return get_task(r['id'])
def emit_event(tid,event_type,payload): execute('INSERT INTO task_events(task_id,event_type,payload_json) VALUES(?,?,?)',(tid,event_type,dumps(payload)))
def list_events(tid,after=0):
    rows=fetch_all('SELECT * FROM task_events WHERE task_id=? AND id>? ORDER BY id',(tid,after))
    for r in rows:r['payload']=loads(r.pop('payload_json','{}'),{})
    return rows
def trace_step(trace,node,input_data,fn):
    st=time.perf_counter()
    try:
        out=fn(); ms=int((time.perf_counter()-st)*1000); execute('INSERT INTO trace_steps(trace_id,node,status,latency_ms,input_json,output_json) VALUES(?,?,?,?,?,?)',(trace,node,'success',ms,dumps(input_data),dumps(out))); return out
    except Exception as e:
        ms=int((time.perf_counter()-st)*1000); execute('INSERT INTO trace_steps(trace_id,node,status,latency_ms,input_json,output_json,error) VALUES(?,?,?,?,?,?,?)',(trace,node,'failed',ms,dumps(input_data),'{}',str(e))); raise
