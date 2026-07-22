"""PostgreSQL connection lifecycle helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import Connection

from common.config import DatabaseSettings


@contextmanager
def database_connection(settings: DatabaseSettings) -> Iterator[Connection[Any]]:
    """Yield one connection and guarantee rollback/close on failure."""
    if settings.database_url:
        connection = psycopg.connect(
            settings.database_url,
            autocommit=False,
            connect_timeout=10,
            application_name="fintech-payments-generator",
        )
    else:
        connection = psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.database,
            user=settings.user,
            password=settings.password,
            autocommit=False,
            connect_timeout=10,
            application_name="fintech-payments-generator",
        )

    try:
        yield connection
    except BaseException:
        try:
            if not connection.closed:
                connection.rollback()
        except psycopg.Error:
            pass
        raise
    finally:
        if not connection.closed:
            connection.close()
