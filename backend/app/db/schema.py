from app.db.database import connect
SCHEMA=r'''
CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY,tenant_id TEXT,user_name TEXT,vip_level TEXT,account_age_days INTEGER,recent_ticket_count INTEGER,recent_refund_count INTEGER,complaint_count_30d INTEGER);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT,amount REAL,status TEXT,payment_status TEXT,shipment_status TEXT,tracking_no TEXT,product_type TEXT,created_at TEXT,delivered_at TEXT);
CREATE TABLE IF NOT EXISTS logistics(order_id TEXT PRIMARY KEY,status TEXT,last_event TEXT,last_update_hours INTEGER,updated_at TEXT,FOREIGN KEY(order_id) REFERENCES orders(id));
CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY,category TEXT,product_type TEXT,title TEXT,version TEXT,status TEXT,effective_from TEXT,content TEXT,keywords_json TEXT);
CREATE TABLE IF NOT EXISTS refund_requests(id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT,order_id TEXT,idempotency_key TEXT UNIQUE,amount REAL,status TEXT,external_refund_id TEXT,reason TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT,order_id TEXT,category TEXT,priority TEXT,status TEXT,description TEXT,assigned_group TEXT,sla_due_at TEXT,source TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,trace_id TEXT,tenant_id TEXT,user_id TEXT,intent TEXT,status TEXT,current_step TEXT,payload_json TEXT,result_json TEXT,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS task_events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,event_type TEXT,payload_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS approval_records(id TEXT PRIMARY KEY,task_id TEXT,order_id TEXT,approver_role TEXT,sequence_no INTEGER,status TEXT,approver_user_id TEXT,comment TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS trace_steps(id INTEGER PRIMARY KEY AUTOINCREMENT,trace_id TEXT,node TEXT,status TEXT,latency_ms INTEGER,input_json TEXT,output_json TEXT,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,trace_id TEXT,actor_id TEXT,action TEXT,detail_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS bad_cases(id INTEGER PRIMARY KEY AUTOINCREMENT,trace_id TEXT,query TEXT,error_type TEXT,root_cause TEXT,expected_result_json TEXT,fixed INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
'''
def init_schema():
    with connect() as c: c.executescript(SCHEMA)
