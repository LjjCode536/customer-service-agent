"""Run deterministic Mock business demonstrations without starting HTTP server."""
from app.core.security import SecurityContext
from app.scripts_seed import seed
from app.services.router import route_intent
from app.services.tasking import create_task, get_task
from app.services.tools import create_ticket, get_logistics
from app.worker import run_once

CASES=[
 ('U1001','订单 O10001 物流两天没更新，到底怎么回事？'),
 ('U1002','订单 O10002 还没发货，我不想要了，帮我退款。'),
 ('U1003','订单 O10003 还没发货，12999元帮我直接退掉。'),
 ('U1004','订单 O10004 再帮我退一次款。'),
 ('U1005','订单 O10005 的东西坏第三次了，再不给我解决我要投诉！'),
]

def main():
    seed()
    for uid,msg in CASES:
        route=route_intent(msg); print('\n',uid,msg,'=>',route)
        if route['intent']=='logistics_query':
            ctx=SecurityContext(uid,'TENANT-DEMO','customer'); log=get_logistics(route['order_id'],ctx); print('logistics=',log)
            if log and log['last_update_hours']>=24: print('ticket=',create_ticket(ctx,route['order_id'],'logistics_abnormal',msg,stale_hours=log['last_update_hours']))
        else:
            ctx=SecurityContext(uid,'TENANT-DEMO','customer'); task=create_task(ctx,route['intent'],{'message':msg,'order_id':route['order_id'],'role':'customer'}); run_once(); print('task=',get_task(task['task_id']))
if __name__=='__main__': main()
