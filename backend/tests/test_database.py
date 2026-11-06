from sqlalchemy import func, inspect, select

from app.core.config import Settings
from app.core.security import SecurityContext
from app.db.database import session_scope
from app.db.models import (
    ApprovalRecord,
    AuditLog,
    BadCase,
    Base,
    Customer,
    Knowledge,
    Logistics,
    Order,
    RefundRequest,
    Task,
    TaskEvent,
    Ticket,
    TraceStep,
)
from app.db.schema import init_schema
from app.scripts_seed import seed
from app.services.tasking import claim_next_task, create_task, list_events
from app.services.tools import get_order, submit_refund


def test_all_domain_entities_have_orm_models():
    expected_tables = {
        'customers',
        'orders',
        'logistics',
        'knowledge',
        'refund_requests',
        'tickets',
        'tasks',
        'task_events',
        'approval_records',
        'trace_steps',
        'audit_logs',
        'bad_cases',
    }
    assert expected_tables <= set(Base.metadata.tables)

    assert Customer.__tablename__ == 'customers'
    assert Order.__tablename__ == 'orders'
    assert Logistics.__tablename__ == 'logistics'
    assert Knowledge.__tablename__ == 'knowledge'
    assert RefundRequest.__tablename__ == 'refund_requests'
    assert Ticket.__tablename__ == 'tickets'
    assert Task.__tablename__ == 'tasks'
    assert TaskEvent.__tablename__ == 'task_events'
    assert ApprovalRecord.__tablename__ == 'approval_records'
    assert TraceStep.__tablename__ == 'trace_steps'
    assert AuditLog.__tablename__ == 'audit_logs'
    assert BadCase.__tablename__ == 'bad_cases'


def test_init_schema_is_idempotent(seeded_db):
    init_schema()
    init_schema()
    assert set(inspect(seeded_db).get_table_names()) >= {'customers', 'orders', 'tasks'}


def test_seed_is_repeatable(seeded_db):
    seed()
    seed()
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Customer)) == 5
        assert session.scalar(select(func.count()).select_from(Order)) == 5
        assert session.scalar(select(func.count()).select_from(Knowledge)) == 4


def test_knowledge_records_embedding_metadata():
    columns = {column.name for column in Knowledge.__table__.columns}
    assert {'embedding', 'embedding_model', 'embedding_generated_at'} <= columns


def test_key_query_indexes_exist(seeded_db):
    inspector = inspect(seeded_db)

    def indexed(table_name, wanted_columns):
        indexes = inspector.get_indexes(table_name)
        unique = inspector.get_unique_constraints(table_name)
        return any(set(wanted_columns) <= set(index['column_names']) for index in indexes) or any(
            set(wanted_columns) <= set(constraint['column_names']) for constraint in unique
        )

    assert indexed('tasks', ['status'])
    assert indexed('approval_records', ['task_id', 'sequence_no'])
    assert indexed('refund_requests', ['idempotency_key'])
    assert indexed('trace_steps', ['trace_id'])


def test_submit_refund_reuses_one_record_for_same_order(seeded_db):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    order = get_order('O10002', context)

    first = submit_refund(context, order, 'agent_refund')
    second = submit_refund(context, order, 'agent_refund')

    assert first['idempotent_reuse'] is False
    assert second['idempotent_reuse'] is True
    assert second['id'] == first['id']
    with session_scope() as session:
        count = session.scalar(
            select(func.count())
            .select_from(RefundRequest)
            .where(RefundRequest.order_id == 'O10002')
        )
        assert count == 1


def test_task_creation_writes_task_and_event_together(seeded_db):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    task = create_task(context, 'refund', {'message': '帮我退款', 'order_id': 'O10002', 'role': 'customer'})

    events = list_events(task['task_id'])
    assert [event['event_type'] for event in events] == ['task_created']


def test_task_claim_is_atomic(seeded_db):
    context = SecurityContext('U1002', 'TENANT-DEMO', 'customer')
    task = create_task(context, 'refund', {'message': '帮我退款', 'order_id': 'O10002', 'role': 'customer'})

    claimed = claim_next_task()
    assert claimed['id'] == task['task_id']
    assert claimed['status'] == 'running'
    assert claim_next_task() is None


def test_models_compile_for_postgres_dialect():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        assert 'CREATE TABLE' in str(CreateTable(table).compile(dialect=dialect))


def test_database_url_can_switch_to_postgres():
    settings = Settings(database_url='postgresql://user:secret@localhost:5432/service')
    assert settings.sqlalchemy_url == 'postgresql+psycopg://user:secret@localhost:5432/service'


def test_default_database_url_is_sqlite():
    settings = Settings(database_url='', database_path='./data/test.db')
    assert settings.sqlalchemy_url == 'sqlite:///data/test.db'
