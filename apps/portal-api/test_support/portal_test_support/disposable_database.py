"""Fail-closed ownership proof for destructive Portal database tests.

This module is test/release tooling. Production runtime code must not import it.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

MARKER_SCHEMA: Final = "portal_test_guard"
MARKER_TABLE: Final = "disposable_database_marker"
MARKER_SCHEMA_VERSION: Final = 1
CREATOR_IDENTITY: Final = "portal-test-orchestrator/v1"

EXPECTED_ROLES: Final = {
    "migration": "portal_migration",
    "runtime": "portal_runtime",
    "archive": "portal_archive",
}

PERSISTENT_DATABASE_DENYLIST: Final = frozenset(
    {
        "airflow",
        "airflow_metadata",
        "keycloak",
        "payments",
        "portal_control",
        "postgres",
        "template0",
        "template1",
    }
)

PERSISTENT_URL_ENVIRONMENT_NAMES: Final = (
    "DATABASE_URL",
    "PORTAL_API_AUDIT_WORKER_DATABASE_URL",
    "PORTAL_API_DATABASE_URL",
    "PORTAL_MIGRATION_DATABASE_URL",
)

PORTAL_AUTHORITY_TABLES: Final = (
    "audit_archive_outbox",
    "audit_delivery_receipts",
    "oidc_login_transactions",
    "portal_capability_definitions",
    "portal_capability_overrides",
    "portal_login_intents",
    "portal_maintenance_jobs",
    "portal_policy_revisions",
    "portal_principals",
    "portal_provider_logout_receipts",
    "portal_security_epochs",
    "portal_sessions",
    "portal_token_envelopes",
    "schema_migrations",
    "security_audit_events",
)

_RUN_ID = re.compile(r"^[a-f0-9]{12,32}$")
_DATABASE_NAME = re.compile(r"^portal_(?:integration|migration)_test_([a-f0-9]{12,32})$")
_COMPOSE_PROJECT = re.compile(r"^portal-(?:integration|migration)-test-([a-f0-9]{12,32})$")


class DisposableDatabaseError(RuntimeError):
    """Safe deterministic refusal without connection secrets."""

    def __init__(self, code: str, safe_reason: str) -> None:
        self.code = code
        self.safe_reason = safe_reason
        super().__init__(f"{code}: {safe_reason}")


@dataclass(frozen=True, slots=True)
class DatabaseEndpoint:
    """Non-secret connection identity parsed from a credential-bearing URL."""

    host: str
    port: int
    database_name: str
    role_name: str


@dataclass(frozen=True, slots=True)
class DatabaseConnectionIdentity:
    """Identity reported by PostgreSQL for one connection."""

    database_name: str
    role_name: str
    is_superuser: bool = False
    can_create_database: bool = False
    can_create_role: bool = False
    can_replicate: bool = False


@dataclass(frozen=True, slots=True)
class DisposableDatabaseIdentity:
    """Expected identity for a run-scoped disposable database."""

    run_id: str
    compose_project: str
    database_name: str
    purpose: str
    migration_url: str = field(repr=False)
    runtime_url: str = field(repr=False)
    archive_url: str = field(repr=False)
    creator_identity: str = CREATOR_IDENTITY


@dataclass(frozen=True, slots=True)
class DisposableDatabaseMarker:
    """Database-owned marker that survives application migration downgrade."""

    marker_schema_version: int
    database_name: str
    run_id: str
    created_at: datetime
    expires_at: datetime
    destructive_nonce_hash: str = field(repr=False)
    creator_identity: str = CREATOR_IDENTITY
    purpose: str = "portal-migration-validation"
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PreparedDestructiveGrant:
    """Marker plus raw one-use token before installation."""

    marker: DisposableDatabaseMarker
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class DestructiveOperationGrant:
    """Proof returned only after the marker token has been consumed."""

    identity: DisposableDatabaseIdentity
    marker_schema_version: int
    consumed_at: datetime


class DisposableDatabaseProbe(Protocol):
    """Storage boundary used by the guard and deterministic unit tests."""

    def connection_identity(self, url: str) -> DatabaseConnectionIdentity: ...

    def read_marker(self, migration_url: str) -> DisposableDatabaseMarker | None: ...

    def authority_counts(self, migration_url: str) -> Mapping[str, int]: ...

    def consume_marker(self, migration_url: str, run_id: str, consumed_at: datetime) -> bool: ...


def _refuse(code: str, reason: str) -> None:
    raise DisposableDatabaseError(code, reason)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _endpoint(url: str, *, label: str) -> DatabaseEndpoint:
    if not url:
        _refuse("DISPOSABLE_URL_MISSING", f"{label} database URL is required")
    try:
        parsed: URL = make_url(url)
    except Exception:
        _refuse("DISPOSABLE_URL_INVALID", f"{label} database URL is malformed")
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
        _refuse("DISPOSABLE_URL_DRIVER", f"{label} database URL must use PostgreSQL")
    if not parsed.host or not parsed.database or not parsed.username:
        _refuse("DISPOSABLE_URL_IDENTITY", f"{label} database URL lacks a safe identity")
    return DatabaseEndpoint(
        host=parsed.host.lower(),
        port=parsed.port or 5432,
        database_name=parsed.database,
        role_name=parsed.username,
    )


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _persistent_database_names(environment: Mapping[str, str]) -> frozenset[str]:
    names = set(PERSISTENT_DATABASE_DENYLIST)
    for variable in PERSISTENT_URL_ENVIRONMENT_NAMES:
        value = environment.get(variable, "")
        if not value:
            continue
        try:
            parsed = make_url(value)
        except Exception:
            _refuse(
                "PERSISTENT_URL_INVALID",
                f"persistent database identity in {variable} is malformed",
            )
        if parsed.database:
            names.add(parsed.database)
    return frozenset(names)


def validate_compose_project(project: str, run_id: str) -> None:
    """Reject missing, ambiguous, or non-run-scoped Compose project names."""

    match = _COMPOSE_PROJECT.fullmatch(project)
    if match is None or match.group(1) != run_id:
        _refuse(
            "COMPOSE_PROJECT_SCOPE",
            "Compose project must be uniquely scoped to the current run",
        )


def create_destructive_grant(
    identity: DisposableDatabaseIdentity,
    *,
    now: datetime | None = None,
    lifetime: timedelta = timedelta(minutes=30),
) -> PreparedDestructiveGrant:
    """Create a token and marker without persisting the raw token."""

    created_at = _utc(now or datetime.now(UTC))
    if lifetime <= timedelta(0) or lifetime > timedelta(hours=2):
        _refuse("TOKEN_LIFETIME_INVALID", "destructive token lifetime is outside the safe bound")
    token = secrets.token_urlsafe(32)
    marker = DisposableDatabaseMarker(
        marker_schema_version=MARKER_SCHEMA_VERSION,
        database_name=identity.database_name,
        run_id=identity.run_id,
        created_at=created_at,
        expires_at=created_at + lifetime,
        destructive_nonce_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        creator_identity=identity.creator_identity,
        purpose=identity.purpose,
    )
    return PreparedDestructiveGrant(marker=marker, token=token)


def _validate_static_identity(
    identity: DisposableDatabaseIdentity,
    *,
    environment: Mapping[str, str],
) -> dict[str, DatabaseEndpoint]:
    if not _RUN_ID.fullmatch(identity.run_id):
        _refuse("RUN_ID_INVALID", "run ID must be a normalized random identifier")
    validate_compose_project(identity.compose_project, identity.run_id)
    persistent_names = _persistent_database_names(environment)
    if identity.database_name in persistent_names:
        _refuse(
            "PERSISTENT_DATABASE_DENIED",
            "database name is reserved or configured as persistent",
        )
    database_match = _DATABASE_NAME.fullmatch(identity.database_name)
    if database_match is None or database_match.group(1) != identity.run_id:
        _refuse(
            "DATABASE_NAME_SCOPE",
            "database name must be uniquely scoped to the current run",
        )
    if identity.creator_identity != CREATOR_IDENTITY:
        _refuse("CREATOR_IDENTITY_MISMATCH", "marker creator identity is not authoritative")
    if identity.purpose not in {
        "portal-integration-validation",
        "portal-migration-validation",
    }:
        _refuse("PURPOSE_INVALID", "disposable database purpose is not authorized")

    endpoints = {
        "migration": _endpoint(identity.migration_url, label="migration"),
        "runtime": _endpoint(identity.runtime_url, label="runtime"),
        "archive": _endpoint(identity.archive_url, label="archive"),
    }
    for label, endpoint in endpoints.items():
        if endpoint.database_name in persistent_names:
            _refuse(
                "PERSISTENT_DATABASE_DENIED",
                f"{label} database name is reserved or configured as persistent",
            )
        if endpoint.database_name != identity.database_name:
            _refuse("DATABASE_IDENTITY_MISMATCH", f"{label} database identity does not match")
        if endpoint.role_name != EXPECTED_ROLES[label]:
            _refuse("DATABASE_ROLE_MISMATCH", f"{label} database role is not least-privileged")
        if not _is_loopback(endpoint.host):
            _refuse(
                "REMOTE_DATABASE_DENIED",
                f"{label} database host is outside the local disposable boundary",
            )
    return endpoints


def _validate_marker(
    identity: DisposableDatabaseIdentity,
    marker: DisposableDatabaseMarker | None,
    *,
    now: datetime,
    require_unconsumed: bool,
) -> DisposableDatabaseMarker:
    if marker is None:
        _refuse("MARKER_MISSING", "disposable database marker is missing")
    if marker.marker_schema_version != MARKER_SCHEMA_VERSION:
        _refuse("MARKER_VERSION_INVALID", "disposable database marker version is unsupported")
    if marker.database_name != identity.database_name:
        _refuse("MARKER_DATABASE_MISMATCH", "marker database identity does not match")
    if marker.run_id != identity.run_id:
        _refuse("MARKER_RUN_MISMATCH", "marker run identity does not match")
    if marker.creator_identity != identity.creator_identity:
        _refuse("MARKER_CREATOR_MISMATCH", "marker creator identity does not match")
    if marker.purpose != identity.purpose:
        _refuse("MARKER_PURPOSE_MISMATCH", "marker purpose does not match")
    if _utc(marker.created_at) > now:
        _refuse("MARKER_TIME_INVALID", "marker creation time is in the future")
    if _utc(marker.expires_at) <= now:
        _refuse("MARKER_EXPIRED", "disposable database marker has expired")
    if require_unconsumed and marker.consumed_at is not None:
        _refuse("TOKEN_ALREADY_CONSUMED", "destructive token has already been consumed")
    if not re.fullmatch(r"[a-f0-9]{64}", marker.destructive_nonce_hash):
        _refuse("MARKER_TOKEN_HASH_INVALID", "marker token verifier is malformed")
    return marker


def assert_disposable_database(
    identity: DisposableDatabaseIdentity,
    probe: DisposableDatabaseProbe,
    *,
    environment: Mapping[str, str],
    now: datetime | None = None,
    require_unconsumed: bool = False,
    require_empty_authority: bool = False,
) -> DisposableDatabaseMarker:
    """Verify all non-secret ownership proofs without consuming the token."""

    current_time = _utc(now or datetime.now(UTC))
    _validate_static_identity(identity, environment=environment)
    expected_urls = {
        "migration": identity.migration_url,
        "runtime": identity.runtime_url,
        "archive": identity.archive_url,
    }
    for label, url in expected_urls.items():
        actual = probe.connection_identity(url)
        if actual.database_name != identity.database_name:
            _refuse("CONNECTED_DATABASE_MISMATCH", f"{label} connected database does not match")
        if actual.role_name != EXPECTED_ROLES[label]:
            _refuse("CONNECTED_ROLE_MISMATCH", f"{label} connected role does not match")
        if (
            actual.is_superuser
            or actual.can_create_database
            or actual.can_create_role
            or actual.can_replicate
        ):
            _refuse(
                "DATABASE_ROLE_EXCESSIVE_AUTHORITY",
                f"{label} database role has excessive cluster authority",
            )

    marker = _validate_marker(
        identity,
        probe.read_marker(identity.migration_url),
        now=current_time,
        require_unconsumed=require_unconsumed,
    )
    if require_empty_authority:
        populated = sorted(
            name for name, count in probe.authority_counts(identity.migration_url).items() if count
        )
        if populated:
            _refuse(
                "PREEXISTING_AUTHORITY_STATE",
                "disposable database contains pre-existing Portal authority state",
            )
    return marker


def consume_destructive_grant(
    identity: DisposableDatabaseIdentity,
    token: str,
    probe: DisposableDatabaseProbe,
    *,
    environment: Mapping[str, str],
    now: datetime | None = None,
) -> DestructiveOperationGrant:
    """Consume the one-use marker token before any database mutation."""

    if not token:
        _refuse("TOKEN_MISSING", "destructive token is required")
    current_time = _utc(now or datetime.now(UTC))
    marker = assert_disposable_database(
        identity,
        probe,
        environment=environment,
        now=current_time,
        require_unconsumed=True,
        require_empty_authority=True,
    )
    supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied_hash, marker.destructive_nonce_hash):
        _refuse("TOKEN_MISMATCH", "destructive token does not match the database marker")
    if not probe.consume_marker(identity.migration_url, identity.run_id, current_time):
        _refuse("TOKEN_ALREADY_CONSUMED", "destructive token has already been consumed")
    return DestructiveOperationGrant(
        identity=identity,
        marker_schema_version=marker.marker_schema_version,
        consumed_at=current_time,
    )


class PostgresDisposableDatabaseProbe:
    """PostgreSQL implementation used only by explicit test orchestration."""

    @staticmethod
    def _engine(url: str):
        return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})

    def connection_identity(self, url: str) -> DatabaseConnectionIdentity:
        engine = self._engine(url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT current_database(), current_user, "
                        "rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                ).one()
            return DatabaseConnectionIdentity(
                database_name=row[0],
                role_name=row[1],
                is_superuser=row[2],
                can_create_database=row[3],
                can_create_role=row[4],
                can_replicate=row[5],
            )
        except DisposableDatabaseError:
            raise
        except Exception:
            _refuse("DATABASE_PROBE_FAILED", "database identity could not be verified")
        finally:
            engine.dispose()

    def read_marker(self, migration_url: str) -> DisposableDatabaseMarker | None:
        engine = self._engine(migration_url)
        try:
            with engine.connect() as connection:
                marker_exists = connection.execute(
                    text("SELECT to_regclass(:table_name)"),
                    {"table_name": f"{MARKER_SCHEMA}.{MARKER_TABLE}"},
                ).scalar_one()
                if marker_exists is None:
                    return None
                rows = connection.execute(
                    text(
                        f"SELECT marker_schema_version, database_name, run_id, created_at, "
                        f"expires_at, destructive_nonce_hash, creator_identity, purpose, "
                        f"consumed_at FROM {MARKER_SCHEMA}.{MARKER_TABLE}"
                    )
                ).all()
            if len(rows) != 1:
                _refuse("MARKER_AMBIGUOUS", "disposable database marker state is ambiguous")
            row = rows[0]
            return DisposableDatabaseMarker(
                marker_schema_version=row.marker_schema_version,
                database_name=row.database_name,
                run_id=row.run_id,
                created_at=_utc(row.created_at),
                expires_at=_utc(row.expires_at),
                destructive_nonce_hash=row.destructive_nonce_hash,
                creator_identity=row.creator_identity,
                purpose=row.purpose,
                consumed_at=_utc(row.consumed_at) if row.consumed_at else None,
            )
        except DisposableDatabaseError:
            raise
        except Exception:
            _refuse("MARKER_READ_FAILED", "disposable database marker could not be read")
        finally:
            engine.dispose()

    def authority_counts(self, migration_url: str) -> Mapping[str, int]:
        engine = self._engine(migration_url)
        counts: dict[str, int] = {}
        try:
            with engine.connect() as connection:
                for table_name in PORTAL_AUTHORITY_TABLES:
                    qualified = f"portal_control.{table_name}"
                    exists = connection.execute(
                        text("SELECT to_regclass(:table_name)"),
                        {"table_name": qualified},
                    ).scalar_one()
                    if exists is not None:
                        counts[table_name] = connection.execute(
                            text(f"SELECT count(*) FROM portal_control.{table_name}")
                        ).scalar_one()
            return counts
        except Exception:
            _refuse(
                "AUTHORITY_STATE_PROBE_FAILED",
                "pre-existing Portal authority state could not be verified",
            )
        finally:
            engine.dispose()

    def consume_marker(self, migration_url: str, run_id: str, consumed_at: datetime) -> bool:
        engine = self._engine(migration_url)
        try:
            with engine.begin() as connection:
                row = connection.execute(
                    text(
                        f"UPDATE {MARKER_SCHEMA}.{MARKER_TABLE} "
                        "SET consumed_at = :consumed_at "
                        "WHERE run_id = :run_id AND consumed_at IS NULL RETURNING run_id"
                    ),
                    {"consumed_at": consumed_at, "run_id": run_id},
                ).first()
            return row is not None
        except Exception:
            _refuse("TOKEN_CONSUME_FAILED", "destructive token could not be consumed")
        finally:
            engine.dispose()


def install_disposable_marker(admin_url: str, prepared: PreparedDestructiveGrant) -> None:
    """Install the single marker using the short-lived database administrator."""

    marker = prepared.marker
    engine = create_engine(admin_url, poolclass=NullPool, connect_args={"connect_timeout": 10})
    try:
        with engine.begin() as connection:
            actual_database = connection.execute(text("SELECT current_database()")).scalar_one()
            if actual_database != marker.database_name:
                _refuse("MARKER_INSTALL_TARGET", "marker installation target does not match")
            connection.execute(text(f"CREATE SCHEMA {MARKER_SCHEMA} AUTHORIZATION CURRENT_USER"))
            connection.execute(
                text(
                    f"CREATE TABLE {MARKER_SCHEMA}.{MARKER_TABLE} ("
                    "marker_schema_version integer NOT NULL, "
                    "database_name text NOT NULL, "
                    "run_id text PRIMARY KEY, "
                    "created_at timestamptz NOT NULL, "
                    "expires_at timestamptz NOT NULL, "
                    "destructive_nonce_hash text NOT NULL, "
                    "creator_identity text NOT NULL, "
                    "purpose text NOT NULL, "
                    "consumed_at timestamptz NULL)"
                )
            )
            connection.execute(
                text(
                    f"INSERT INTO {MARKER_SCHEMA}.{MARKER_TABLE} ("
                    "marker_schema_version, database_name, run_id, created_at, expires_at, "
                    "destructive_nonce_hash, creator_identity, purpose) VALUES ("
                    ":marker_schema_version, :database_name, :run_id, :created_at, :expires_at, "
                    ":destructive_nonce_hash, :creator_identity, :purpose)"
                ),
                {
                    "marker_schema_version": marker.marker_schema_version,
                    "database_name": marker.database_name,
                    "run_id": marker.run_id,
                    "created_at": marker.created_at,
                    "expires_at": marker.expires_at,
                    "destructive_nonce_hash": marker.destructive_nonce_hash,
                    "creator_identity": marker.creator_identity,
                    "purpose": marker.purpose,
                },
            )
            connection.execute(text(f"REVOKE ALL ON SCHEMA {MARKER_SCHEMA} FROM PUBLIC"))
            connection.execute(text(f"REVOKE ALL ON {MARKER_SCHEMA}.{MARKER_TABLE} FROM PUBLIC"))
            connection.execute(text(f"GRANT USAGE ON SCHEMA {MARKER_SCHEMA} TO portal_migration"))
            connection.execute(
                text(f"GRANT SELECT ON {MARKER_SCHEMA}.{MARKER_TABLE} TO portal_migration")
            )
            connection.execute(
                text(
                    f"GRANT UPDATE (consumed_at) ON {MARKER_SCHEMA}.{MARKER_TABLE} "
                    "TO portal_migration"
                )
            )
    except DisposableDatabaseError:
        raise
    except Exception:
        _refuse("MARKER_INSTALL_FAILED", "disposable database marker could not be installed")
    finally:
        engine.dispose()
