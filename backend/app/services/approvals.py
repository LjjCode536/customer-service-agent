from sqlalchemy import func, select, update

from app.db.database import session_scope, utcnow
from app.db.models import ApprovalRecord, Task
from app.services.tasking import record_audit


class ApprovalNotFound(LookupError):
    pass


class ApprovalSequenceConflict(RuntimeError):
    pass


def decide_approval(
    approval_id: str,
    status: str,
    approver_user_id: str = 'agent-demo',
    comment: str = '',
) -> dict:
    """Apply one approval decision and return the whole chain for the task."""
    if status not in {'approved', 'rejected'}:
        raise ValueError('status must approved/rejected')

    with session_scope() as session:
        approval = session.get(ApprovalRecord, approval_id)
        if not approval:
            raise ApprovalNotFound('approval not found')
        prior = session.scalars(
            select(ApprovalRecord)
            .where(
                ApprovalRecord.task_id == approval.task_id,
                ApprovalRecord.sequence_no < approval.sequence_no,
                ApprovalRecord.status == 'pending',
            )
            .order_by(ApprovalRecord.sequence_no)
            .limit(1)
        ).first()
        if prior:
            raise ApprovalSequenceConflict(f"请先完成前置审批：{prior.approver_role}")

        task = session.get(Task, approval.task_id)
        approval.status = status
        approval.approver_user_id = approver_user_id
        approval.comment = comment
        approval.updated_at = utcnow()
        cancelled = 0
        if status == 'rejected':
            result = session.execute(
                update(ApprovalRecord)
                .where(
                    ApprovalRecord.task_id == approval.task_id,
                    ApprovalRecord.status == 'pending',
                    ApprovalRecord.id != approval_id,
                )
                .values(status='cancelled', updated_at=func.current_timestamp())
            )
            cancelled = result.rowcount or 0

        session.flush()
        all_rows = [
            row.to_dict()
            for row in session.scalars(
                select(ApprovalRecord).where(
                    ApprovalRecord.task_id == approval.task_id
                )
            )
        ]
        decision = approval.to_dict()
        record_audit(
            task.trace_id if task else None,
            approver_user_id,
            f'approval_{status}',
            {
                'approval_id': approval.id,
                'task_id': approval.task_id,
                'order_id': approval.order_id,
                'approver_role': approval.approver_role,
                'sequence_no': approval.sequence_no,
                'comment': comment,
                'cancelled_approvals': cancelled,
            },
            session=session,
        )
        if (
            any(row['status'] == 'rejected' for row in all_rows)
            or not any(row['status'] == 'pending' for row in all_rows)
        ) and task:
            task.status = 'resume_pending'
            task.current_step = 'approval_resume'
            task.updated_at = utcnow()
            session.flush()
        return {'approval': decision, 'all': all_rows}
