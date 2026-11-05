import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fd, path = tempfile.mkstemp(suffix='.db')
os.close(fd)
os.environ.setdefault('DATABASE_PATH', path)
