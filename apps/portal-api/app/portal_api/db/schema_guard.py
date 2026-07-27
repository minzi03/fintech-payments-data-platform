"""Fail-closed runtime compatibility check for the versioned security schema."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

SUPPORTED_SCHEMA_VERSION = "002_login_intent_and_initiation"


class SchemaCompatibilityError(RuntimeError):
    """Raised when the runtime cannot prove a compatible Portal schema."""


@dataclass(frozen=True)
class SchemaCompatibility:
    version: str
    application_compat: str


def validate_runtime_schema(engine: Engine) -> SchemaCompatibility:
    """Read the migration-owned compatibility projection and reject unknown revisions."""
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT version, application_compat "
                    "FROM portal_control.current_schema_compatibility()"
                )
            ).one_or_none()
    except SQLAlchemyError as error:
        raise SchemaCompatibilityError(
            "Portal security schema compatibility could not be verified"
        ) from error

    if row is None:
        raise SchemaCompatibilityError("Portal security schema has no authoritative revision")
    compatibility = SchemaCompatibility(
        version=str(row.version),
        application_compat=str(row.application_compat),
    )
    if compatibility.version != SUPPORTED_SCHEMA_VERSION:
        raise SchemaCompatibilityError(
            "Portal security schema revision is incompatible with this application"
        )
    return compatibility
