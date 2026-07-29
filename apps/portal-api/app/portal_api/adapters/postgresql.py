"""Read-only PostgreSQL readiness checks for the Portal security authority."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from portal_api.adapters.models import (
    AdapterHealthResult,
    AdapterIdentity,
    DependencyStatus,
)
from portal_api.db.schema_guard import (
    SUPPORTED_SCHEMA_VERSION,
    SchemaCompatibilityError,
    validate_runtime_schema,
)

POSTGRESQL_ADAPTER_ID = "portal-postgresql"

_RUNTIME_PERMISSION_QUERY = text(
    """
    SELECT
      has_schema_privilege(current_user, 'portal_control', 'USAGE')
      AND has_function_privilege(
        current_user,
        'portal_control.current_schema_compatibility()',
        'EXECUTE'
      )
      AND has_function_privilege(
        current_user,
        'portal_control.append_security_audit_event(jsonb)',
        'EXECUTE'
      )
      AND has_table_privilege(
        current_user,
        'portal_control.portal_principals',
        'SELECT,INSERT,UPDATE'
      )
      AND has_table_privilege(
        current_user,
        'portal_control.portal_login_intents',
        'SELECT,INSERT,UPDATE'
      )
      AND has_table_privilege(
        current_user,
        'portal_control.oidc_login_transactions',
        'SELECT,INSERT,UPDATE'
      )
      AND has_table_privilege(
        current_user,
        'portal_control.portal_sessions',
        'SELECT,INSERT,UPDATE'
      )
      AND has_table_privilege(
        current_user,
        'portal_control.portal_token_envelopes',
        'SELECT,INSERT,UPDATE,DELETE'
      )
    """
)


class PostgreSqlReadinessAdapter:
    """Verify connectivity, schema authority, migration head, and runtime privileges."""

    identity = AdapterIdentity(
        adapter_id=POSTGRESQL_ADAPTER_ID,
        display_name="Portal PostgreSQL",
        dependency_type="database",
        required=True,
        version=f"schema-{SUPPORTED_SCHEMA_VERSION}",
    )

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def check_health(self) -> AdapterHealthResult:
        try:
            permissions_valid = await asyncio.to_thread(self._check)
        except SchemaCompatibilityError:
            return self._result(
                DependencyStatus.UNAVAILABLE,
                "Portal schema compatibility could not be verified.",
            )
        except SQLAlchemyError:
            return self._result(
                DependencyStatus.UNAVAILABLE,
                "Portal database connectivity or permissions could not be verified.",
            )
        if not permissions_valid:
            return self._result(
                DependencyStatus.UNAVAILABLE,
                "Required Portal runtime database permissions are unavailable.",
            )
        return self._result(
            DependencyStatus.UP,
            f"Portal schema {SUPPORTED_SCHEMA_VERSION} and runtime permissions are available.",
        )

    def _check(self) -> bool:
        validate_runtime_schema(self._engine)
        with self._engine.connect() as connection:
            return bool(connection.execute(_RUNTIME_PERMISSION_QUERY).scalar_one())

    def _result(self, status: DependencyStatus, reason: str) -> AdapterHealthResult:
        return AdapterHealthResult(
            identity=self.identity,
            status=status,
            observed_at=datetime.now(UTC),
            reason=reason,
        )
