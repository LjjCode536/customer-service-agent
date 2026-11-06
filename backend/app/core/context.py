from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, TypeVar

T = TypeVar('T')


@contextmanager
def scoped(var: ContextVar[T], value: T) -> Iterator[T]:
    """Set a context-local override for the duration of the block."""
    token = var.set(value)
    try:
        yield value
    finally:
        var.reset(token)
