import time
from app.services.tasking import claim_next_task,update_task
from app.workflow.service_graph import process_task,resume_after_approval

def run_once():
    task=claim_next_task()
    if not task:return False
    try:
        if task['status']=='running' and task['current_step']=='approval_resume': resume_after_approval(task)
        else: process_task(task)
    except Exception as e:
        update_task(task['id'],status='failed',current_step='failed',error=str(e)); print('task failed',task['id'],e)
    return True
if __name__=='__main__':
    print('worker started')
    while True:
        if not run_once(): time.sleep(0.6)
