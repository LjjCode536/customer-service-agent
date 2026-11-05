import json, sqlite3
from contextlib import contextmanager
from app.core.config import settings

def dumps(v): return json.dumps(v, ensure_ascii=False, default=str)
def loads(v, default=None):
    if v is None or v == '': return default
    try: return json.loads(v)
    except Exception: return default

@contextmanager
def connect():
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn; conn.commit()
    finally: conn.close()

def execute(sql, params=()):
    with connect() as c:
        cur=c.execute(sql,params); return cur.lastrowid

def fetch_one(sql, params=()):
    with connect() as c:
        r=c.execute(sql,params).fetchone(); return dict(r) if r else None

def fetch_all(sql, params=()):
    with connect() as c: return [dict(r) for r in c.execute(sql,params).fetchall()]
