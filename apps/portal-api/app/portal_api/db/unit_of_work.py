"""Explicit local database transaction boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Connection, Engine


@contextmanager
def local_transaction(engine: Engine) -> Iterator[Connection]:
    """Yield one local transaction.

    Callers must complete all external network work before entering this boundary.
    """
    with engine.begin() as connection:
        yield connection
