import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


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


def _create_engine(database_url: str):
    settings.ensure_dirs()
    kwargs = {'pool_pre_ping': True}
    if database_url.startswith('sqlite'):
        kwargs['connect_args'] = {'check_same_thread': False, 'timeout': 30}
        if database_url in {'sqlite://', 'sqlite:///:memory:'} or ':memory:' in database_url:
            kwargs['poolclass'] = StaticPool
    return create_engine(database_url, **kwargs)


engine = _create_engine(settings.sqlalchemy_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA busy_timeout=30000')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction per logical unit of work. Roll back all writes on error."""
    session = SessionLocal()
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
