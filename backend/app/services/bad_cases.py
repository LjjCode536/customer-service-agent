from sqlalchemy import select

from app.db.database import dumps, loads, session_scope
from app.db.models import BadCase

UPDATABLE_FIELDS = {'trace_id', 'query', 'error_type', 'root_cause', 'fixed'}


def _serialize(row: BadCase) -> dict:
    data = row.to_dict()
    data['expected_result'] = loads(data.pop('expected_result_json', '{}'), {})
    data['fixed'] = bool(data['fixed'])
    return data


def list_bad_cases(trace_id: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = select(BadCase).order_by(BadCase.id.desc())
        if trace_id:
            query = query.where(BadCase.trace_id == trace_id)
        rows = session.scalars(query)
        return [_serialize(row) for row in rows]


def get_bad_case(bad_case_id: int) -> dict | None:
    with session_scope() as session:
        row = session.get(BadCase, bad_case_id)
        return _serialize(row) if row else None


def create_bad_case(
    *,
    query: str,
    error_type: str,
    trace_id: str | None = None,
    root_cause: str = '',
    expected_result: dict | None = None,
) -> dict:
    with session_scope() as session:
        row = BadCase(
            trace_id=trace_id,
            query=query,
            error_type=error_type,
            root_cause=root_cause,
            expected_result_json=dumps(expected_result or {}),
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return _serialize(row)


def update_bad_case(bad_case_id: int, changes: dict) -> dict | None:
    with session_scope() as session:
        row = session.get(BadCase, bad_case_id)
        if not row:
            return None
        for field, value in changes.items():
            if field == 'expected_result':
                row.expected_result_json = dumps(value or {})
            elif field in UPDATABLE_FIELDS:
                setattr(row, field, value)
        session.flush()
        session.refresh(row)
        return _serialize(row)
