"""Explicit local database transaction boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from portal_api.telemetry.database import telemetry_for_engine


@contextmanager
def local_transaction(engine: Engine) -> Iterator[Connection]:
    """Yield one local transaction.

    Callers must complete all external network work before entering this boundary.
    """
    telemetry = telemetry_for_engine(engine)
    started = perf_counter()
    acquired = False
    try:
        with engine.begin() as connection:
            acquired = True
            if telemetry is not None:
                telemetry.record_database_connection(
                    "success",
                    (perf_counter() - started) * 1000,
                )
            yield connection
    except SQLAlchemyError:
        if not acquired and telemetry is not None:
            telemetry.record_database_connection(
                "error",
                (perf_counter() - started) * 1000,
            )
        raise
