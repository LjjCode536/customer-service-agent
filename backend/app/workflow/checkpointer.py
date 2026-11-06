import sqlite3
from pathlib import Path
from typing import Any

from app.core.config import settings


def create_checkpointer(path: str | None = None) -> Any:
    """Open the durable SQLite checkpoint store used by Human Review pauses."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    settings.ensure_dirs()
    target = path or settings.checkpoint_file
    if target != ':memory:':
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    saver = SqliteSaver(conn)
    saver.setup()
    return saver
