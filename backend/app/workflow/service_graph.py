from typing import TypedDict
from app.core.security import SecurityContext
from app.db.database import execute,fetch_all,fetch_one,dumps,loads
from app.llm.provider import get_provider
from app.rag.hybrid import retrieve
from app.services.rules import refund_eligibility,refund_risk,approval_chain
from app.services.tools import get_customer,get_order,get_logistics,get_refunds,create_ticket,submit_refund
from app.services.tasking import emit_event,trace_step,update_task,new_id

class ServiceState(TypedDict,total=False):
    task_id:str; trace_id:str; tenant_id:str; user_id:str; role:str; intent:str; message:str; order_id:str
    customer:dict; order:dict; logistics:dict; knowledge:list[dict]; refunds:list[dict]; eligibility:dict; risk:dict; approval_chain:list[str]; ticket:dict; refund:dict; answer:str

def ctx(s): return SecurityContext(s['user_id'],s['tenant_id'],s.get('role','customer'))
def _node_customer(s): return {'customer':get_customer(ctx(s))}
def _node_order(s): return {'order':get_order(s['order_id'],ctx(s))}
def _node_logistics(s): return {'logistics':get_logistics(s['order_id'],ctx(s)) or {}}
def _node_knowledge(s):
    category='logistics' if s['intent']=='complaint' else 'refund'
    return {'knowledge':retrieve(s['message'],category=category,product_type=s.get('order',{}).get('product_type'),top_n=4)}
def _node_refunds(s): return {'refunds':get_refunds(s['order_id'],ctx(s))}
def _node_eligibility(s): return {'eligibility':refund_eligibility(s['order'],s.get('refunds',[]))}
def _node_risk(s):
    r=refund_risk(s['order'],s['customer'],s['eligibility']); return {'risk':r,'approval_chain':approval_chain(r)}
def _node_ticket(s):
    if s['intent']=='complaint':
        t=create_ticket(ctx(s),s.get('order_id'),'complaint',s['message'],complaint=True,stale_hours=int(s.get('logistics',{}).get('last_update_hours') or 0))
    else:
        t=create_ticket(ctx(s),s.get('order_id'),'refund_duplicate',s['message'])
    return {'ticket':t}
def _node_answer(s):
    if s.get('ticket'):
        base=f"已为你创建工单 {s['ticket']['id']}，优先级 {s['ticket']['priority']}，已分派到 {s['ticket']['assigned_group']}。"
    elif s.get('refund'):
        base=f"退款申请已提交，金额 {float(s['refund']['amount']):.2f} 元，退款单号 {s['refund']['external_refund_id']}。"
    elif s.get('eligibility',{}).get('reason')=='DUPLICATE_REFUND_REQUEST':
        base='该订单已经存在退款申请，为避免重复退款，本次不会再次创建退款。'
    else:
        base='客服流程已完成。'
    evidence={'message':s['message'],'order':s.get('order'),'logistics':s.get('logistics'),'eligibility':s.get('eligibility'),'risk':s.get('risk'),'ticket':s.get('ticket'),'refund':s.get('refund'),'knowledge':[{'title':x['title'],'content':x['content']} for x in s.get('knowledge',[])]}
    return {'answer':get_provider().chat('你是企业客服助手。只能解释系统已经完成的业务结果，不得改变退款结果、风险等级或工单优先级。',f'请用简洁自然的中文回复用户：{dumps(evidence)}',mock_response=base)}

NODES={'load_customer':_node_customer,'load_order':_node_order,'load_logistics':_node_logistics,'retrieve_policy':_node_knowledge,'load_refunds':_node_refunds,'check_eligibility':_node_eligibility,'risk_check':_node_risk,'create_ticket':_node_ticket,'answer':_node_answer}
def run_node(name,s):
    emit_event(s['task_id'],'node_start',{'node':name})
    out=trace_step(s['trace_id'],name,{'intent':s['intent'],'order_id':s.get('order_id')},lambda:NODES[name](s)); emit_event(s['task_id'],'node_completed',{'node':name,'output':out}); return out

def _fallback(state):
    s=dict(state)
    for n in ['load_customer','load_order']:
        s.update(run_node(n,s))
    if s['intent']=='complaint':
        if s['order'].get('tracking_no'): s.update(run_node('load_logistics',s))
        s.update(run_node('retrieve_policy',s)); s.update(run_node('create_ticket',s)); s.update(run_node('answer',s)); return s
    for n in ['retrieve_policy','load_refunds','check_eligibility','risk_check']:
        s.update(run_node(n,s))
    if s['eligibility']['reason']=='DUPLICATE_REFUND_REQUEST':
        s.update(run_node('create_ticket',s)); s.update(run_node('answer',s)); return s
    if s['risk']['risk_level']=='high':
        s.update(run_node('answer',s)); return s
    if s['eligibility']['eligible']:
        s['refund']=submit_refund(ctx(s),s['order'],'agent_refund')
    else:
        s['ticket']=create_ticket(ctx(s),s['order_id'],'refund_review',s['message'])
    s.update(run_node('answer',s)); return s

def _build_graph():
    try:
        from langgraph.graph import StateGraph,END
    except Exception:return None
    g=StateGraph(ServiceState)
    for name in NODES:
        def make(n):
            return lambda s: run_node(n,s)
        g.add_node(name,make(name))
    def decision_after_order(s): return 'complaint' if s['intent']=='complaint' else 'refund'
    def after_eligibility(s):
        if s['eligibility']['reason']=='DUPLICATE_REFUND_REQUEST': return 'duplicate'
        return 'risk'
    def after_risk(s): return 'human' if s['risk']['risk_level']=='high' else 'finish'
    g.set_entry_point('load_customer'); g.add_edge('load_customer','load_order')
    g.add_conditional_edges('load_order',decision_after_order,{'complaint':'retrieve_policy','refund':'retrieve_policy'})
    g.add_conditional_edges('retrieve_policy',lambda s:'complaint' if s['intent']=='complaint' else 'refund',{'complaint':'create_ticket','refund':'load_refunds'})
    g.add_edge('load_refunds','check_eligibility'); g.add_conditional_edges('check_eligibility',after_eligibility,{'duplicate':'create_ticket','risk':'risk_check'})
    # Real side effects and human approval are finalized outside the graph to preserve idempotency and durable approval state.
    g.add_edge('risk_check','answer'); g.add_edge('create_ticket','answer'); g.add_edge('answer',END)
    return g.compile()
GRAPH=_build_graph()

def ensure_approvals(task_id,order_id,chain):
    old=fetch_one('SELECT COUNT(*) c FROM approval_records WHERE task_id=?',(task_id,))
    if old and old['c']:return
    for i,r in enumerate(chain,1): execute('INSERT INTO approval_records(id,task_id,order_id,approver_role,sequence_no,status) VALUES(?,?,?,?,?,?)',(new_id('approval'),task_id,order_id,r,i,'pending'))

def process_task(task):
    p=task['payload']; state:ServiceState={'task_id':task['id'],'trace_id':task['trace_id'],'tenant_id':task['tenant_id'],'user_id':task['user_id'],'role':p.get('role','customer'),'intent':task['intent'],'message':p['message'],'order_id':p.get('order_id')}
    update_task(task['id'],status='running',current_step='workflow'); emit_event(task['id'],'workflow_start',{'intent':task['intent']})
    result=GRAPH.invoke(state) if GRAPH is not None else _fallback(state)
    # LangGraph path deliberately postpones side effects until here.
    if GRAPH is not None and task['intent']=='refund':
        if result.get('eligibility',{}).get('reason')=='DUPLICATE_REFUND_REQUEST':
            if not result.get('ticket'): result['ticket']=create_ticket(ctx(result),result['order_id'],'refund_duplicate',result['message'])
        elif result.get('risk',{}).get('risk_level')!='high':
            if result.get('eligibility',{}).get('eligible'): result['refund']=submit_refund(ctx(result),result['order'],'agent_refund')
            else: result['ticket']=create_ticket(ctx(result),result['order_id'],'refund_review',result['message'])
        result.update(_node_answer(result))
    if task['intent']=='refund' and result.get('risk',{}).get('risk_level')=='high' and result.get('eligibility',{}).get('reason')!='DUPLICATE_REFUND_REQUEST':
        chain=result.get('approval_chain') or ['refund_specialist','service_manager']; ensure_approvals(task['id'],result['order_id'],chain)
        payload={'status':'waiting_approval','risk':result['risk'],'approval_chain':chain,'eligibility':result['eligibility'],'answer':'该退款金额较高，已进入人工审核，审核通过后系统会继续原退款流程。'}
        update_task(task['id'],status='waiting_approval',current_step='human_review',result=payload); emit_event(task['id'],'human_review_required',payload); return payload
    payload={'status':'completed','answer':result.get('answer'),'ticket':result.get('ticket'),'refund':result.get('refund'),'risk':result.get('risk'),'eligibility':result.get('eligibility')}
    update_task(task['id'],status='completed',current_step='done',result=payload); emit_event(task['id'],'task_completed',payload)
    execute('INSERT INTO audit_logs(trace_id,actor_id,action,detail_json) VALUES(?,?,?,?)',(task['trace_id'],task['user_id'],'service_workflow_completed',dumps(payload)))
    return payload

def resume_after_approval(task):
    rows=fetch_all('SELECT * FROM approval_records WHERE task_id=? ORDER BY sequence_no',(task['id'],))
    if any(r['status']=='rejected' for r in rows):
        payload={'status':'rejected','answer':'人工审核未通过，本次退款不会执行。'}; update_task(task['id'],status='completed',current_step='rejected',result=payload); emit_event(task['id'],'task_completed',payload); return payload
    if any(r['status']=='pending' for r in rows):
        update_task(task['id'],status='waiting_approval',current_step='human_review'); return {'status':'waiting_approval'}
    p=task['payload']; c=SecurityContext(task['user_id'],task['tenant_id'],p.get('role','customer')); order=get_order(p['order_id'],c); refund=submit_refund(c,order,'approved_high_value_refund')
    payload={'status':'completed','answer':f"人工审核已通过，退款 {float(refund['amount']):.2f} 元已提交，退款单号 {refund['external_refund_id']}。",'refund':refund}
    update_task(task['id'],status='completed',current_step='refund_submitted',result=payload); emit_event(task['id'],'task_completed',payload); return payload
