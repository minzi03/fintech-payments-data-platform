"""Alembic environment for the migration-owned Portal security schema."""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from portal_api.db.metadata import metadata
from portal_api.db.migration_integrity import (
    MIGRATION_LOCK_KEY,
    validate_recorded_migration_checksums,
)
from sqlalchemy import engine_from_config, pool, text

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata
VERSIONS_DIRECTORY = Path(__file__).resolve().parent / "versions"


def _include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    del object_, reflected, compare_to
    return not (type_ == "table" and name == "alembic_version")


def _migration_url() -> str:
    url = os.environ.get("PORTAL_MIGRATION_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("PORTAL_MIGRATION_DATABASE_URL is required")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Portal migrations require PostgreSQL with psycopg")
    return url


def run_migrations_offline() -> None:
    """Offline SQL cannot prove locks or authoritative checksum state."""
    raise RuntimeError("Offline Portal security migrations are forbidden")


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _migration_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        connection.execute(
            text("CREATE SCHEMA IF NOT EXISTS portal_migration AUTHORIZATION portal_migration")
        )
        connection.commit()
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": MIGRATION_LOCK_KEY},
        ).scalar_one()
        if not acquired:
            raise RuntimeError("Another Portal migration runner holds the advisory lock")
        try:
            validate_recorded_migration_checksums(connection, VERSIONS_DIRECTORY)
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                include_schemas=True,
                include_object=_include_object,
                transaction_per_migration=True,
                version_table_schema="portal_migration",
            )
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if connection.in_transaction():
                connection.rollback()
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": MIGRATION_LOCK_KEY},
            )
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
