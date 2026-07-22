"""Idempotent least-privilege PostgreSQL CDC role and publication bootstrap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import Connection, sql

from common.config import ConfigurationError

from .config import CAPTURED_QUALIFIED_TABLES, CdcSettings


@dataclass(frozen=True, slots=True)
class PostgresAdminSettings:
    """Local bootstrap administrator connection with password hidden from repr."""

    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> PostgresAdminSettings:
        required = {
            name: environ.get(name, "").strip()
            for name in (
                "POSTGRES_HOST",
                "POSTGRES_PORT",
                "POSTGRES_DB",
                "POSTGRES_USER",
                "POSTGRES_PASSWORD",
            )
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError(
                f"Missing PostgreSQL bootstrap variables: {', '.join(missing)}"
            )
        try:
            port = int(required["POSTGRES_PORT"])
        except ValueError as error:
            raise ConfigurationError("POSTGRES_PORT must be an integer") from error
        if not 1 <= port <= 65535:
            raise ConfigurationError("POSTGRES_PORT must be between 1 and 65535")
        return cls(
            host=required["POSTGRES_HOST"],
            port=port,
            database=required["POSTGRES_DB"],
            user=required["POSTGRES_USER"],
            password=required["POSTGRES_PASSWORD"],
        )


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Non-secret evidence from one PostgreSQL bootstrap execution."""

    role_created: bool
    publication_created: bool
    publication_name: str
    captured_table_count: int


def bootstrap_postgres_cdc(
    admin: PostgresAdminSettings,
    cdc: CdcSettings,
    *,
    connection: Connection[Any] | None = None,
) -> BootstrapResult:
    """Create or reconcile a non-superuser replication role and explicit publication."""
    if cdc.database_user == admin.user:
        raise ConfigurationError(
            "DEBEZIUM_DATABASE_USER must differ from the PostgreSQL bootstrap administrator"
        )
    owns_connection = connection is None
    active_connection = connection or psycopg.connect(
        host=admin.host,
        port=admin.port,
        dbname=admin.database,
        user=admin.user,
        password=admin.password,
        autocommit=True,
        connect_timeout=10,
    )
    try:
        return _bootstrap_with_connection(active_connection, admin, cdc)
    finally:
        if owns_connection:
            active_connection.close()


def _bootstrap_with_connection(
    connection: Connection[Any],
    admin: PostgresAdminSettings,
    cdc: CdcSettings,
) -> BootstrapResult:
    role_exists = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
        (cdc.database_user,),
    ).fetchone()
    role_created = not bool(role_exists and role_exists[0])
    role = sql.Identifier(cdc.database_user)
    password = sql.Literal(cdc.database_password)
    if role_created:
        connection.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN REPLICATION PASSWORD {}").format(role, password)
        )
    else:
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN REPLICATION NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE PASSWORD {}"
            ).format(role, password)
        )

    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(admin.database), role)
    )
    connection.execute(sql.SQL("GRANT USAGE ON SCHEMA payments TO {}").format(role))
    table_list = captured_table_sql()
    connection.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(table_list, role))
    connection.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA payments GRANT SELECT ON TABLES TO {}").format(
            role
        )
    )

    publication_exists = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = %s)",
        (cdc.publication_name,),
    ).fetchone()
    publication_created = not bool(publication_exists and publication_exists[0])
    publication = sql.Identifier(cdc.publication_name)
    if publication_created:
        connection.execute(
            sql.SQL("CREATE PUBLICATION {} FOR TABLE {}").format(publication, table_list)
        )
    else:
        connection.execute(
            sql.SQL("ALTER PUBLICATION {} SET TABLE {}").format(publication, table_list)
        )

    role_state = connection.execute(
        "SELECT rolreplication, rolsuper FROM pg_roles WHERE rolname = %s",
        (cdc.database_user,),
    ).fetchone()
    if role_state != (True, False):
        raise RuntimeError("CDC role must have replication privilege and must not be superuser")
    return BootstrapResult(
        role_created=role_created,
        publication_created=publication_created,
        publication_name=cdc.publication_name,
        captured_table_count=len(CAPTURED_QUALIFIED_TABLES),
    )


def captured_table_sql() -> sql.Composed:
    """Return safely quoted explicit publication/grant table identifiers."""
    return sql.SQL(", ").join(
        sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
        for schema, table in (name.split(".", maxsplit=1) for name in CAPTURED_QUALIFIED_TABLES)
    )
