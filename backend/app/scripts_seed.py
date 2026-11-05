from app.db.schema import init_schema
from app.db.database import connect, dumps
from app.fixtures.mock_data import CUSTOMERS,ORDERS,LOGISTICS,KNOWLEDGE

def seed():
    init_schema()
    with connect() as c:
        for t in ['approval_records','task_events','tasks','tickets','trace_steps','audit_logs','bad_cases','refund_requests','logistics','orders','customers','knowledge']:
            c.execute(f'DELETE FROM {t}')
        for x in CUSTOMERS: c.execute('INSERT INTO customers VALUES(?,?,?,?,?,?,?,?)',(x['id'],'TENANT-DEMO',x['user_name'],x['vip_level'],x['account_age_days'],x['recent_ticket_count'],x['recent_refund_count'],x['complaint_count_30d']))
        for x in ORDERS: c.execute('INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)',(x['id'],'TENANT-DEMO',x['user_id'],x['amount'],x['status'],x['payment_status'],x['shipment_status'],x['tracking_no'],x['product_type'],x['created_at'],x['delivered_at']))
        for x in LOGISTICS: c.execute('INSERT INTO logistics VALUES(?,?,?,?,?)',(x['order_id'],x['status'],x['last_event'],x['last_update_hours'],x['updated_at']))
        for x in KNOWLEDGE: c.execute('INSERT INTO knowledge VALUES(?,?,?,?,?,?,?,?,?)',(x['id'],x['category'],x['product_type'],x['title'],x['version'],x['status'],x['effective_from'],x['content'],dumps(x['keywords'])))
        c.execute("INSERT INTO refund_requests(id,tenant_id,user_id,order_id,idempotency_key,amount,status,external_refund_id,reason) VALUES(?,?,?,?,?,?,?,?,?)",('RR-HISTORY-10004','TENANT-DEMO','U1004','O10004','refund:TENANT-DEMO:O10004',899,'accepted','RF-HISTORY-10004','historical_refund'))
        c.execute("INSERT INTO tickets(id,tenant_id,user_id,order_id,category,priority,status,description,assigned_group,sla_due_at,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",('T-HISTORY-U1005-1','TENANT-DEMO','U1005','O10005','after_sales','P2','resolved','商品故障第一次售后','after_sales','2026-09-06T10:00:00','human'))
        c.execute("INSERT INTO tickets(id,tenant_id,user_id,order_id,category,priority,status,description,assigned_group,sla_due_at,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",('T-HISTORY-U1005-2','TENANT-DEMO','U1005','O10005','after_sales','P2','resolved','商品故障第二次售后','after_sales','2026-09-10T10:00:00','human'))
    print('seed complete')
if __name__=='__main__': seed()
