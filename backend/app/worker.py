import time

from app.documents.ingestion import run_ingestion_once
from app.services.tasking import claim_next_task, emit_event, update_task
from app.workflow.service_graph import process_task, resume_after_approval


def run_once():
    """Drain at most one workflow task and one ingestion job per loop.

    Both queues are served in the same loop so a busy task queue cannot starve
    document uploads.
    """
    handled = False
    task = claim_next_task()
    if task:
        try:
            if task['status'] == 'running' and task['current_step'] == 'approval_resume':
                resume_after_approval(task)
            else:
                process_task(task)
        except Exception as e:  # noqa: BLE001  # task boundary: any failure must be recorded, never crash the loop
            update_task(task['id'], status='failed', current_step='failed', error=str(e))
            emit_event(task['id'], 'task_failed', {'status': 'failed', 'error': str(e)})
            print('task failed', task['id'], e)
        handled = True
    try:
        if run_ingestion_once():
            handled = True
    except Exception as e:  # noqa: BLE001  # ingestion boundary: keep the worker alive
        print('ingestion failed', e)
    return handled


if __name__ == '__main__':
    print('worker started')
    while True:
        if not run_once():
            time.sleep(0.6)
