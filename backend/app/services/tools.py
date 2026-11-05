from datetime import datetime,timedelta
from app.core.security import SecurityContext
from app.db.database import fetch_one,fetch_all,execute,dumps
from app.services.tasking import new_id

def get_customer(ctx: SecurityContext):
    row=fetch_one('SELECT * FROM customers WHERE id=? AND tenant_id=?',(ctx.user_id,ctx.tenant_id))
    if not row: raise PermissionError('CUSTOMER_NOT_FOUND')
    return row

def get_order(order_id, ctx: SecurityContext):
    row=fetch_one('SELECT * FROM orders WHERE id=? AND tenant_id=?',(order_id,ctx.tenant_id))
    if not row: raise ValueError('ORDER_NOT_FOUND')
    if row['user_id']!=ctx.user_id and ctx.role not in {'agent','admin'}: raise PermissionError('ORDER_ACCESS_DENIED')
    return row

def get_logistics(order_id, ctx):
    get_order(order_id,ctx); return fetch_one('SELECT * FROM logistics WHERE order_id=?',(order_id,))

def get_refunds(order_id,ctx):
    get_order(order_id,ctx); return fetch_all('SELECT * FROM refund_requests WHERE order_id=? ORDER BY created_at DESC',(order_id,))

def priority(category, vip_level, complaint=False, stale_hours=0):
    if complaint and vip_level=='vip': return 'P1'
    if category in {'refund_failed','payment_issue'} or stale_hours>=24: return 'P2'
    return 'P3'

def sla_due(p):
    mins={'P0':15,'P1':30,'P2':120,'P3':480}[p]
    return (datetime.now()+timedelta(minutes=mins)).isoformat(timespec='seconds')

def assigned_group(category):
    return {'logistics_abnormal':'logistics_support','refund_review':'after_sales','complaint':'complaint_specialist','refund_duplicate':'after_sales'}.get(category,'customer_service')

def create_ticket(ctx, order_id, category, description, complaint=False, stale_hours=0):
    existing=fetch_one("SELECT * FROM tickets WHERE tenant_id=? AND user_id=? AND order_id IS ? AND category=? AND status='open' ORDER BY created_at DESC LIMIT 1",(ctx.tenant_id,ctx.user_id,order_id,category))
    if existing: return existing
    customer=get_customer(ctx); p=priority(category,customer['vip_level'],complaint,stale_hours); tid=new_id('ticket')
    execute('''INSERT INTO tickets(id,tenant_id,user_id,order_id,category,priority,status,description,assigned_group,sla_due_at,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(tid,ctx.tenant_id,ctx.user_id,order_id,category,p,'open',description,assigned_group(category),sla_due(p),'agent'))
    return fetch_one('SELECT * FROM tickets WHERE id=?',(tid,))

def submit_refund(ctx,order,reason):
    key=f"refund:{ctx.tenant_id}:{order['id']}"; old=fetch_one('SELECT * FROM refund_requests WHERE idempotency_key=?',(key,))
    if old: return {**old,'idempotent_reuse':True}
    rid=new_id('refund'); external='RF-'+rid[-8:].upper()
    execute('''INSERT INTO refund_requests(id,tenant_id,user_id,order_id,idempotency_key,amount,status,external_refund_id,reason) VALUES(?,?,?,?,?,?,?,?,?)''',(rid,ctx.tenant_id,ctx.user_id,order['id'],key,order['amount'],'accepted',external,reason))
    execute("UPDATE orders SET status='refund_processing' WHERE id=?",(order['id'],))
    return {**fetch_one('SELECT * FROM refund_requests WHERE id=?',(rid,)),'idempotent_reuse':False}
