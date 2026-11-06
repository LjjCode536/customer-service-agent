from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    def to_dict(self) -> dict[str, Any]:
        """Serialize a model into the dict shape the existing API already returns."""
        result = {}
        for column in self.__table__.columns:
            value = getattr(self, column.name)
            if isinstance(value, datetime):
                # The pre-SQLAlchemy API exposed "YYYY-MM-DD HH:MM:SS" strings.
                value = value.isoformat(sep=' ', timespec='seconds')
            result[column.name] = value
        return result


class Customer(Base):
    __tablename__ = 'customers'
    __table_args__ = (Index('ix_customers_tenant_id', 'tenant_id'),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64))
    user_name: Mapped[str | None] = mapped_column(String(128))
    vip_level: Mapped[str | None] = mapped_column(String(32))
    account_age_days: Mapped[int | None] = mapped_column(Integer)
    recent_ticket_count: Mapped[int | None] = mapped_column(Integer)
    recent_refund_count: Mapped[int | None] = mapped_column(Integer)
    complaint_count_30d: Mapped[int | None] = mapped_column(Integer)


class Order(Base):
    __tablename__ = 'orders'
    __table_args__ = (
        Index('ix_orders_tenant_user', 'tenant_id', 'user_id'),
        Index('ix_orders_status', 'status'),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(32))
    payment_status: Mapped[str | None] = mapped_column(String(32))
    shipment_status: Mapped[str | None] = mapped_column(String(32))
    tracking_no: Mapped[str | None] = mapped_column(String(128))
    product_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str | None] = mapped_column(String(64))
    delivered_at: Mapped[str | None] = mapped_column(String(64))


class Logistics(Base):
    __tablename__ = 'logistics'

    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey('orders.id'), primary_key=True
    )
    status: Mapped[str | None] = mapped_column(String(32))
    last_event: Mapped[str | None] = mapped_column(String(255))
    last_update_hours: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[str | None] = mapped_column(String(64))


class Knowledge(Base):
    __tablename__ = 'knowledge'
    __table_args__ = (
        Index('ix_knowledge_status_effective_from', 'status', 'effective_from'),
        Index('ix_knowledge_category_product_type', 'category', 'product_type'),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str | None] = mapped_column(String(64))
    product_type: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    effective_from: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(Text)
    # Kept as JSON text for backwards-compatible API output; parsed by app.db.database.loads.
    keywords_json: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(JSON)
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_generated_at: Mapped[datetime | None] = mapped_column(DateTime)


class RefundRequest(Base):
    __tablename__ = 'refund_requests'
    __table_args__ = (
        Index('ix_refund_requests_order_id', 'order_id'),
        Index('ix_refund_requests_tenant_user', 'tenant_id', 'user_id'),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    amount: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(32))
    external_refund_id: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class Ticket(Base):
    __tablename__ = 'tickets'
    __table_args__ = (
        Index(
            'ix_tickets_lookup',
            'tenant_id',
            'user_id',
            'order_id',
            'category',
            'status',
        ),
        Index('ix_tickets_status_priority', 'status', 'priority'),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    assigned_group: Mapped[str | None] = mapped_column(String(64))
    sla_due_at: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class Task(Base):
    __tablename__ = 'tasks'
    __table_args__ = (
        Index('ix_tasks_status_created_at', 'status', 'created_at'),
        Index('ix_tasks_trace_id', 'trace_id'),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    tenant_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(64))
    intent: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    current_step: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )


class TaskEvent(Base):
    __tablename__ = 'task_events'
    __table_args__ = (Index('ix_task_events_task_id_id', 'task_id', 'id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class ApprovalRecord(Base):
    __tablename__ = 'approval_records'
    __table_args__ = (
        Index(
            'ix_approval_records_task_id_sequence_no', 'task_id', 'sequence_no'
        ),
        Index('ix_approval_records_status', 'status'),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(64))
    approver_role: Mapped[str | None] = mapped_column(String(64))
    sequence_no: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(32))
    approver_user_id: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )


class TraceStep(Base):
    __tablename__ = 'trace_steps'
    __table_args__ = (Index('ix_trace_steps_trace_id_id', 'trace_id', 'id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    node: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_json: Mapped[str | None] = mapped_column(Text)
    output_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    __table_args__ = (Index('ix_audit_logs_trace_id', 'trace_id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(64))
    detail_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


class BadCase(Base):
    __tablename__ = 'bad_cases'
    __table_args__ = (Index('ix_bad_cases_trace_id', 'trace_id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    query: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(64))
    root_cause: Mapped[str | None] = mapped_column(Text)
    expected_result_json: Mapped[str | None] = mapped_column(Text)
    fixed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


# The issue names the entities "Trace" and "Audit"; the domain glossary distinguishes
# Trace (the whole chain) from TraceStep (one persisted node), so TraceStep/AuditLog stay
# canonical and these aliases only smooth over the ticket vocabulary.
Trace = TraceStep
Audit = AuditLog
