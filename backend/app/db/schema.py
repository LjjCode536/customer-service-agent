from app.db.database import engine
from app.db.models import Base


def init_schema():
    """Create any missing tables. Safe to call on every startup and in tests."""
    Base.metadata.create_all(engine)
