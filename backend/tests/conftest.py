import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Tests must never touch a developer or CI database configured through .env.
fd, path = tempfile.mkstemp(suffix='.db')
os.close(fd)
os.environ['DATABASE_PATH'] = path
os.environ['DATABASE_URL'] = ''
os.environ['DOCUMENT_STORAGE_DIR'] = tempfile.mkdtemp(prefix='documents-')


@pytest.fixture()
def fake_provider():
    from app.llm.provider import reset_provider, set_provider
    from tests.fakes import DeterministicProvider

    provider = DeterministicProvider()
    reset_provider()
    set_provider(provider)
    yield provider
    reset_provider()


@pytest.fixture()
def seeded_db(fake_provider):
    from app.db.database import engine
    from app.db.schema import init_schema
    from app.scripts_seed import seed

    init_schema()
    seed()
    return engine
