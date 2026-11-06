import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.context import scoped


def utcnow() -> datetime:
    """Naive UTC, matching SQLite/Postgres CURRENT_TIMESTAMP defaults."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dumps(v):
    return json.dumps(v, ensure_ascii=False, default=str)


def loads(v, default=None):
    if v is None or v == '':
        return default
    try:
        return json.loads(v)
    except Exception:
        return default


def create_engine_for_url(database_url: str):
    """Build an engine for any SQLAlchemy URL, including isolated evaluation databases."""
    settings.ensure_dirs()
    kwargs = {'pool_pre_ping': True}
    if database_url.startswith('sqlite'):
        kwargs['connect_args'] = {'check_same_thread': False, 'timeout': 30}
        if database_url in {'sqlite://', 'sqlite:///:memory:'} or ':memory:' in database_url:
            kwargs['poolclass'] = StaticPool
    target = create_engine(database_url, **kwargs)
    if target.dialect.name == 'sqlite':
        @event.listens_for(target, 'connect')
        def _set_sqlite_pragmas(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA busy_timeout=30000')
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()
    return target


engine = create_engine_for_url(settings.sqlalchemy_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)

# Evaluation and tests can bind a sandbox database for the current context only.
_engine_override: ContextVar[Engine | None] = ContextVar('engine_override', default=None)
_session_factory_override: ContextVar[sessionmaker | None] = ContextVar(
    'session_factory_override', default=None
)


def active_engine() -> Engine:
    return _engine_override.get() or engine


def active_session_factory() -> sessionmaker:
    return _session_factory_override.get() or SessionLocal


@contextmanager
def use_database_engine(target: Engine) -> Iterator[sessionmaker]:
    """Bind all session_scope() calls in this context to an isolated database."""
    factory = sessionmaker(
        bind=target, autoflush=False, expire_on_commit=False, class_=Session
    )
    with scoped(_engine_override, target), scoped(
        _session_factory_override, factory
    ):
        yield factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction per logical unit of work. Roll back all writes on error."""
    session = active_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def use_session(session: Session | None = None) -> Iterator[Session]:
    """Reuse a caller-owned transaction, or open a committed one when none was passed."""
    if session is not None:
        yield session
        return
    with session_scope() as owned:
        yield owned
