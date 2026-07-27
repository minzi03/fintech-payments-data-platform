"""Run the governed F-003 local/development database restore evidence drill.

This opt-in harness creates isolated source and restored databases. It does
not modify or remove the normal Portal database or any existing local data.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PORTAL_ROOT = REPOSITORY_ROOT / "apps" / "portal-api"
sys.path.insert(0, str(PORTAL_ROOT / "app"))

from portal_api.audit.ledger import AuditLedger  # noqa: E402
from portal_api.auth.callback import CallbackOrchestrator  # noqa: E402
from portal_api.auth.policy import LocalDevelopmentCallbackPolicy  # noqa: E402
from portal_api.auth.ports import ProviderTokenSet, ValidatedIdentity  # noqa: E402
from portal_api.auth.principal import ConfiguredPrincipalResolver  # noqa: E402
from portal_api.auth.protected_value import ProtectedValue  # noqa: E402
from portal_api.auth.security_material import EphemeralSecurityMaterial  # noqa: E402
from portal_api.auth.session_store import CallbackSessionStore  # noqa: E402
from portal_api.core.config import PortalApiSettings, PortalEnvironment  # noqa: E402
from portal_api.main import _security_components, create_app  # noqa: E402

SOURCE_MASTER_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
ROTATED_MASTER_KEY = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="
SOURCE_KEY_VERSION = "f003-restore-v1"
ROTATED_KEY_VERSION = "f003-restore-v2"
EXPECTED_PROVIDER_TOKENS = {
    "access_token": "f003-evidence-access-token",
    "refresh_token": "f003-evidence-refresh-token",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


@dataclass(frozen=True)
class Check:
    criterion: str
    status: str
    evidence: str


class EvidenceRecorder:
    def __init__(self) -> None:
        self.started_at = iso_now()
        self.commands: list[dict[str, str]] = []
        self.events: list[dict[str, str]] = []
        self.checks: list[Check] = []

    def event(self, phase: str, action: str, result: str) -> None:
        self.events.append(
            {
                "timestamp": iso_now(),
                "phase": phase,
                "action": action,
                "result": result,
            }
        )

    def command(self, phase: str, rendered_command: str, result: str) -> None:
        self.commands.append(
            {
                "timestamp": iso_now(),
                "phase": phase,
                "command": rendered_command,
                "result": result,
            }
        )

    def check(self, criterion: str, condition: bool, evidence: str) -> None:
        self.checks.append(
            Check(
                criterion=criterion,
                status="PASS" if condition else "FAIL",
                evidence=evidence,
            )
        )


class EvidenceProvider:
    async def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet:
        if code != "provider-code" or not verifier:
            raise AssertionError("The controlled provider exchange input is invalid")
        if redirect_uri != "http://portal.test/portal-api/v1/auth/callback":
            raise AssertionError("The controlled redirect URI is invalid")
        return ProviderTokenSet(
            id_token="f003-evidence-id-token",
            access_token=EXPECTED_PROVIDER_TOKENS["access_token"],
            refresh_token=EXPECTED_PROVIDER_TOKENS["refresh_token"],
            token_type="Bearer",
            expires_in=300,
        )

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, object]:
        del force_refresh
        raise AssertionError("The controlled validator does not fetch JWKS")


class EvidenceTokenValidator:
    async def validate(
        self,
        *,
        id_token: str,
        expected_nonce_hash: bytes,
    ) -> ValidatedIdentity:
        if id_token != "f003-evidence-id-token" or not expected_nonce_hash:
            raise AssertionError("The controlled token validation input is invalid")
        now = utc_now()
        return ValidatedIdentity(
            issuer="http://identity.test/realms/portal",
            subject="f003-restore-subject",
            nonce="f003-controlled-nonce",
            groups=("portal_role:portal_viewer", "portal_env:local"),
            display_name="F-003 Restore Evidence",
            assurance="AAL1",
            authenticated_at=now,
            token_expires_at=now + timedelta(minutes=5),
        )


def git_output(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def database_url(role: str, database: str) -> str:
    return f"postgresql+psycopg://{role}:change_me@127.0.0.1:55432/{database}"


def settings_for(
    database: str,
    *,
    current_master_key: str,
    current_key_version: str,
    session_security_epoch: int,
    previous_master_key: str | None = None,
    previous_key_version: str | None = None,
    transition_started_at: datetime | None = None,
    transition_expires_at: datetime | None = None,
) -> PortalApiSettings:
    return PortalApiSettings(
        environment=PortalEnvironment.TEST,
        service_version="0.1.0-f003-restore-evidence",
        build_sha=git_output("rev-parse", "HEAD"),
        build_time=iso_now(),
        log_level="WARNING",
        log_format="json",
        allowed_origins="http://portal.test",
        trusted_hosts="testserver,portal.test",
        security_runtime_enabled=True,
        database_url=database_url("portal_runtime", database),
        security_master_key=current_master_key,
        security_key_version=current_key_version,
        security_previous_master_key=previous_master_key,
        security_previous_key_version=previous_key_version,
        security_key_transition_started_at=transition_started_at,
        security_key_transition_expires_at=transition_expires_at,
        session_security_epoch=session_security_epoch,
        oidc_issuer="http://identity.test/realms/portal",
        oidc_authorization_endpoint="http://identity.test/authorize",
        oidc_token_endpoint="http://identity.test/token",
        oidc_jwks_uri="http://identity.test/jwks",
        oidc_redirect_uri="http://portal.test/portal-api/v1/auth/callback",
    )


def attach_controlled_callback(app: Any, settings: PortalApiSettings) -> None:
    runtime_engine = app.state.database_engine
    material = app.state.security_material
    cipher = app.state.protected_value_cipher
    if not isinstance(runtime_engine, Engine):
        raise RuntimeError("Portal runtime engine was not created")
    if not isinstance(material, EphemeralSecurityMaterial) or cipher is None:
        raise RuntimeError("Portal security material was not created")
    app.state.callback_orchestrator = CallbackOrchestrator(
        engine=runtime_engine,
        settings=settings,
        security_material=material,
        protected_value_cipher=cipher,
        provider=EvidenceProvider(),
        token_validator=EvidenceTokenValidator(),
        principal_resolver=ConfiguredPrincipalResolver(
            engine=runtime_engine,
            settings=settings,
        ),
        policy=LocalDevelopmentCallbackPolicy(settings),
        session_store=CallbackSessionStore(
            engine=runtime_engine,
            settings=settings,
            security_material=material,
            protected_value_cipher=cipher,
            audit_ledger=AuditLedger(),
        ),
    )


def begin_login(client: TestClient) -> str:
    context = client.get("/v1/auth/login-context")
    if context.status_code != 200:
        raise RuntimeError(f"Login context failed: {context.status_code}")
    response = client.post(
        "/v1/auth/login",
        headers={"Origin": "http://portal.test"},
        json={"intent_token": context.json()["intent_token"]},
    )
    if response.status_code != 303:
        raise RuntimeError(f"Login initiation failed: {response.status_code}")
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    if not isinstance(state, str):
        raise RuntimeError("Login initiation returned a non-string state")
    return state


def complete_login(client: TestClient) -> Any:
    state = begin_login(client)
    response = client.get(
        "/v1/auth/callback",
        params={"state": state, "code": "provider-code"},
    )
    if response.status_code != 303:
        raise RuntimeError(f"Controlled callback failed: {response.status_code}")
    return response


def create_database(database: str, recorder: EvidenceRecorder) -> None:
    admin = "postgresql://portal_admin:change_me@127.0.0.1:55432/postgres"
    with psycopg.connect(admin, autocommit=True) as connection:
        existing = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (database,),
        ).fetchone()
        if existing is not None:
            raise RuntimeError(f"Evidence database already exists: {database}")
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER portal_migration").format(sql.Identifier(database))
        )
        connection.execute(
            sql.SQL(
                "GRANT CONNECT ON DATABASE {} TO portal_runtime, portal_audit, portal_archive"
            ).format(sql.Identifier(database))
        )
    recorder.command(
        "database-preparation",
        f"CREATE DATABASE {database} OWNER portal_migration; GRANT CONNECT ...",
        "completed",
    )


def migrate_source(database: str, recorder: EvidenceRecorder) -> None:
    config = Config(str(PORTAL_ROOT / "alembic.ini"))
    previous = os.environ.get("PORTAL_MIGRATION_DATABASE_URL")
    os.environ["PORTAL_MIGRATION_DATABASE_URL"] = database_url("portal_migration", database)
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("PORTAL_MIGRATION_DATABASE_URL", None)
        else:
            os.environ["PORTAL_MIGRATION_DATABASE_URL"] = previous
    recorder.command(
        "database-preparation",
        f"alembic -c apps/portal-api/alembic.ini upgrade head [{database}]",
        "completed",
    )


def run_external(
    arguments: list[str],
    *,
    phase: str,
    rendered: str,
    recorder: EvidenceRecorder,
) -> None:
    completed = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    recorder.command(phase, rendered, f"exit={completed.returncode}")
    if completed.returncode != 0:
        raise RuntimeError(
            f"External command failed ({rendered}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def backup_and_restore(
    source_database: str,
    restored_database: str,
    backup_path: Path,
    recorder: EvidenceRecorder,
) -> None:
    container_backup = f"/tmp/{backup_path.name}"
    run_external(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "portal-postgres",
            "pg_dump",
            "--username=portal_admin",
            "--format=custom",
            f"--dbname={source_database}",
            f"--file={container_backup}",
        ],
        phase="backup",
        rendered=(
            "docker compose exec -T portal-postgres pg_dump "
            f"--username=portal_admin --format=custom --dbname={source_database} "
            f"--file={container_backup}"
        ),
        recorder=recorder,
    )
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    run_external(
        [
            "docker",
            "compose",
            "cp",
            f"portal-postgres:{container_backup}",
            str(backup_path),
        ],
        phase="backup",
        rendered=(
            f"docker compose cp portal-postgres:{container_backup} "
            f"tmp/f003-restore-evidence/{backup_path.name}"
        ),
        recorder=recorder,
    )
    run_external(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "portal-postgres",
            "pg_restore",
            "--username=portal_admin",
            "--no-owner",
            "--exit-on-error",
            f"--dbname={restored_database}",
            container_backup,
        ],
        phase="restore",
        rendered=(
            "docker compose exec -T portal-postgres pg_restore "
            f"--username=portal_admin --no-owner --exit-on-error "
            f"--dbname={restored_database} {container_backup}"
        ),
        recorder=recorder,
    )


def envelope_from_row(row: dict[str, Any]) -> ProtectedValue:
    combined = bytes(row["ciphertext"]) + bytes(row["authentication_tag"])
    return ProtectedValue(
        ciphertext=base64.urlsafe_b64encode(combined).decode("ascii"),
        nonce=base64.urlsafe_b64encode(bytes(row["nonce"])).decode("ascii"),
        wrapped_data_key=base64.urlsafe_b64encode(bytes(row["wrapped_data_key"])).decode("ascii"),
        wrapped_data_key_nonce=base64.urlsafe_b64encode(
            bytes(row["wrapped_data_key_nonce"])
        ).decode("ascii"),
        key_reference=str(row["kms_key_id"]),
    )


def query_one(
    engine: Engine,
    statement: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with engine.connect() as connection:
        result = connection.execute(text(statement), parameters or {}).mappings().one()
    return dict(result)


def query_scalar(
    engine: Engine,
    statement: str,
    parameters: dict[str, Any] | None = None,
) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(statement), parameters or {}).scalar_one()


def run_drill(output_directory: Path) -> dict[str, Any]:
    recorder = EvidenceRecorder()
    run_identifier = utc_now().strftime("%Y%m%dT%H%M%SZ")
    source_database = f"portal_f003_source_{run_identifier.lower()}"
    restored_database = f"portal_f003_restored_{run_identifier.lower()}"
    backup_path = output_directory / f"{source_database}.dump"
    commit_sha = git_output("rev-parse", "HEAD")
    tree_sha = git_output("rev-parse", "HEAD^{tree}")

    recorder.event("environment", "immutable application revision", commit_sha)
    create_database(source_database, recorder)
    create_database(restored_database, recorder)
    migrate_source(source_database, recorder)

    source_engine = create_engine(database_url("portal_migration", source_database))
    source_settings = settings_for(
        source_database,
        current_master_key=SOURCE_MASTER_KEY,
        current_key_version=SOURCE_KEY_VERSION,
        session_security_epoch=1,
    )
    source_app = create_app(settings=source_settings)
    attach_controlled_callback(source_app, source_settings)
    with TestClient(source_app, follow_redirects=False) as source_client:
        authenticated = complete_login(source_client)
        session_secret = authenticated.cookies.get("fintech_portal_session_v1")
        if not session_secret:
            raise RuntimeError("Controlled login did not create a session cookie")
        source_client.cookies.set("fintech_portal_session_v1", session_secret)
        source_session_response = source_client.get("/v1/session")
        pending_state = begin_login(source_client)
        pending_binding = source_client.cookies.get("fintech_portal_oidc_binding_v1")
        if not pending_binding:
            raise RuntimeError("Controlled login did not create a browser-binding cookie")

    original_session = query_one(
        source_engine,
        """
        SELECT session_id, session_family_id, status, security_epoch, lookup_key_version
        FROM portal_control.portal_sessions ORDER BY created_at LIMIT 1
        """,
    )
    pending_transaction = query_one(
        source_engine,
        """
        SELECT transaction_id, status, browser_binding_key_version
        FROM portal_control.oidc_login_transactions
        WHERE status = 'PENDING' ORDER BY created_at DESC LIMIT 1
        """,
    )
    original_envelope = query_one(
        source_engine,
        """
        SELECT session_family_id, ciphertext, nonce, authentication_tag,
               wrapped_data_key, wrapped_data_key_nonce, kms_key_id
        FROM portal_control.portal_token_envelopes
        WHERE session_family_id = :session_family_id
        """,
        {"session_family_id": original_session["session_family_id"]},
    )
    source_schema_head = query_scalar(
        source_engine,
        "SELECT version_num FROM portal_migration.alembic_version",
    )
    recorder.check(
        "Pre-backup session exists and is usable",
        source_session_response.status_code == 200 and original_session["status"] == "ACTIVE",
        (
            f"HTTP {source_session_response.status_code}; status={original_session['status']}; "
            f"epoch={original_session['security_epoch']}; "
            f"lookup_key_version={original_session['lookup_key_version']}"
        ),
    )
    recorder.check(
        "Pre-backup pending login exists",
        pending_transaction["status"] == "PENDING",
        (
            f"status={pending_transaction['status']}; "
            f"browser_binding_key_version={pending_transaction['browser_binding_key_version']}"
        ),
    )
    recorder.check(
        "Pre-backup protected provider envelope exists",
        original_envelope["kms_key_id"] == SOURCE_KEY_VERSION,
        f"kms_key_id={original_envelope['kms_key_id']}",
    )
    recorder.event("seed", "source runtime stopped", "completed before backup")
    source_engine.dispose()

    backup_started_at = iso_now()
    backup_and_restore(source_database, restored_database, backup_path, recorder)
    backup_completed_at = iso_now()
    backup_digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    recorder.event(
        "backup",
        "backup artifact preservation",
        f"sha256={backup_digest}; size={backup_path.stat().st_size}",
    )

    # Post-restore evidence inspection uses the local database administrator.
    # The runtime itself still connects exclusively as portal_runtime below.
    restored_engine = create_engine(database_url("portal_admin", restored_database))
    restored_schema_head = query_scalar(
        restored_engine,
        "SELECT version_num FROM portal_migration.alembic_version",
    )
    restored_migration_count = query_scalar(
        restored_engine,
        "SELECT count(*) FROM portal_control.schema_migrations",
    )
    runtime_schema_access = query_scalar(
        restored_engine,
        "SELECT has_schema_privilege('portal_runtime', 'portal_control', 'USAGE')",
    )
    runtime_session_access = query_scalar(
        restored_engine,
        """
        SELECT has_table_privilege(
            'portal_runtime',
            'portal_control.portal_sessions',
            'SELECT,INSERT,UPDATE'
        )
        """,
    )
    restored_pending_status = query_scalar(
        restored_engine,
        """
        SELECT status FROM portal_control.oidc_login_transactions
        WHERE transaction_id = :transaction_id
        """,
        {"transaction_id": pending_transaction["transaction_id"]},
    )
    restored_session_status = query_scalar(
        restored_engine,
        """
        SELECT status FROM portal_control.portal_sessions
        WHERE session_id = :session_id
        """,
        {"session_id": original_session["session_id"]},
    )
    recorder.check(
        "Database restore preserves the governed schema revision",
        source_schema_head == restored_schema_head == "006_session_revocation_fence",
        f"source={source_schema_head}; restored={restored_schema_head}",
    )
    recorder.check(
        "Database restore preserves governed runtime access",
        bool(runtime_schema_access) and bool(runtime_session_access),
        (
            f"portal_control_usage={runtime_schema_access}; "
            f"portal_sessions_select_insert_update={runtime_session_access}"
        ),
    )
    recorder.check(
        "Restore preserves the pending transaction before restart",
        restored_pending_status == "PENDING",
        f"restored status={restored_pending_status}",
    )
    recorder.check(
        "Restore preserves the pre-start session row for epoch invalidation",
        restored_session_status == "ACTIVE",
        f"pre-start restored status={restored_session_status}",
    )

    transition_started_at = utc_now() - timedelta(minutes=1)
    transition_expires_at = utc_now() + timedelta(minutes=10)
    restored_settings = settings_for(
        restored_database,
        current_master_key=ROTATED_MASTER_KEY,
        current_key_version=ROTATED_KEY_VERSION,
        previous_master_key=SOURCE_MASTER_KEY,
        previous_key_version=SOURCE_KEY_VERSION,
        transition_started_at=transition_started_at,
        transition_expires_at=transition_expires_at,
        session_security_epoch=2,
    )
    restored_app = create_app(settings=restored_settings)
    attach_controlled_callback(restored_app, restored_settings)
    with TestClient(restored_app, follow_redirects=False) as restored_client:
        restored_client.cookies.set("fintech_portal_session_v1", session_secret)
        old_session_response = restored_client.get("/v1/session")
        restored_client.cookies.set("fintech_portal_oidc_binding_v1", pending_binding)
        callback_response = restored_client.get(
            "/v1/auth/callback",
            params={"state": pending_state, "code": "provider-code"},
        )
        new_session_secret = callback_response.cookies.get("fintech_portal_session_v1")

    post_restart_session = query_one(
        restored_engine,
        """
        SELECT status, security_epoch, lookup_key_version, revoked_reason
        FROM portal_control.portal_sessions WHERE session_id = :session_id
        """,
        {"session_id": original_session["session_id"]},
    )
    restored_pending_after_callback = query_scalar(
        restored_engine,
        """
        SELECT status FROM portal_control.oidc_login_transactions
        WHERE transaction_id = :transaction_id
        """,
        {"transaction_id": pending_transaction["transaction_id"]},
    )
    new_session = query_one(
        restored_engine,
        """
        SELECT status, security_epoch, lookup_key_version, session_family_id
        FROM portal_control.portal_sessions
        WHERE session_id <> :original_session_id
        ORDER BY created_at DESC LIMIT 1
        """,
        {"original_session_id": original_session["session_id"]},
    )
    epoch_audit_reason = query_scalar(
        restored_engine,
        """
        SELECT reason_code FROM portal_control.security_audit_events
        WHERE session_reference = :session_reference
          AND event_type = 'auth.session_revoked.v1'
        ORDER BY ledger_sequence DESC LIMIT 1
        """,
        {"session_reference": str(original_session["session_id"])},
    )
    restored_envelope = query_one(
        restored_engine,
        """
        SELECT session_family_id, ciphertext, nonce, authentication_tag,
               wrapped_data_key, wrapped_data_key_nonce, kms_key_id
        FROM portal_control.portal_token_envelopes
        WHERE session_family_id = :session_family_id
        """,
        {"session_family_id": original_session["session_family_id"]},
    )
    old_protected = envelope_from_row(restored_envelope)
    restored_cipher = restored_app.state.protected_value_cipher
    decrypted_tokens = json.loads(
        restored_cipher.decrypt(
            old_protected,
            context=str(original_session["session_family_id"]).encode("ascii"),
        ).decode("utf-8")
    )
    recorder.check(
        "Restored session is invalidated authoritatively after security-epoch bump",
        (
            old_session_response.status_code == 401
            and post_restart_session["status"] == "INVALID"
            and epoch_audit_reason == "SESSION_SECURITY_EPOCH_INVALID"
        ),
        (
            f"HTTP {old_session_response.status_code}; "
            f"status={post_restart_session['status']}; reason={epoch_audit_reason}; "
            f"configured_epoch=2; stored_epoch={post_restart_session['security_epoch']}"
        ),
    )
    recorder.check(
        "Restored pending login remains processable during the governed key transition",
        callback_response.status_code == 303 and restored_pending_after_callback == "CONSUMED",
        (
            f"HTTP {callback_response.status_code}; "
            f"transaction_status={restored_pending_after_callback}"
        ),
    )
    recorder.check(
        "Post-restore callback creates a current-epoch, current-key session",
        (
            bool(new_session_secret)
            and new_session["status"] == "ACTIVE"
            and new_session["security_epoch"] == 2
            and new_session["lookup_key_version"] == ROTATED_KEY_VERSION
        ),
        (
            f"cookie_created={bool(new_session_secret)}; status={new_session['status']}; "
            f"epoch={new_session['security_epoch']}; "
            f"lookup_key_version={new_session['lookup_key_version']}"
        ),
    )
    recorder.check(
        "Restored protected provider envelope decrypts through its recorded previous key",
        (
            restored_envelope["kms_key_id"] == SOURCE_KEY_VERSION
            and decrypted_tokens == EXPECTED_PROVIDER_TOKENS
        ),
        (
            f"kms_key_id={restored_envelope['kms_key_id']}; "
            f"plaintext_fields={sorted(decrypted_tokens)}"
        ),
    )

    unavailable_settings = settings_for(
        restored_database,
        current_master_key=ROTATED_MASTER_KEY,
        current_key_version=ROTATED_KEY_VERSION,
        session_security_epoch=2,
    )
    _, unavailable_cipher = _security_components(unavailable_settings)
    unavailable_failed_closed = False
    try:
        assert unavailable_cipher is not None
        unavailable_cipher.decrypt(
            old_protected,
            context=str(original_session["session_family_id"]).encode("ascii"),
        )
    except ValueError:
        unavailable_failed_closed = True
    recorder.check(
        "Unavailable previous key version fails closed",
        unavailable_failed_closed,
        f"recorded_key={old_protected.key_reference}; available_key={ROTATED_KEY_VERSION}",
    )

    unsupported_failed_closed = False
    try:
        restored_cipher.decrypt(
            replace(old_protected, key_reference="f003-unsupported-v999"),
            context=str(original_session["session_family_id"]).encode("ascii"),
        )
    except ValueError:
        unsupported_failed_closed = True
    recorder.check(
        "Unsupported key version fails closed",
        unsupported_failed_closed,
        "f003-unsupported-v999 was rejected",
    )

    expired_settings = settings_for(
        restored_database,
        current_master_key=ROTATED_MASTER_KEY,
        current_key_version=ROTATED_KEY_VERSION,
        previous_master_key=SOURCE_MASTER_KEY,
        previous_key_version=SOURCE_KEY_VERSION,
        transition_started_at=utc_now() - timedelta(minutes=20),
        transition_expires_at=utc_now() - timedelta(minutes=10),
        session_security_epoch=2,
    )
    _, expired_cipher = _security_components(expired_settings)
    retired_failed_closed = False
    try:
        assert expired_cipher is not None
        expired_cipher.decrypt(
            old_protected,
            context=str(original_session["session_family_id"]).encode("ascii"),
        )
    except ValueError:
        retired_failed_closed = True
    recorder.check(
        "Retired previous key fails closed after deterministic transition expiry",
        retired_failed_closed,
        (
            f"previous_key={SOURCE_KEY_VERSION}; current_key={ROTATED_KEY_VERSION}; "
            "transition window expired before verification"
        ),
    )

    all_passed = all(check.status == "PASS" for check in recorder.checks)
    restored_engine.dispose()
    return {
        "artifact_type": "F-003 Restore-Evidence Execution Result",
        "execution_status": "COMPLETED" if all_passed else "PARTIALLY_COMPLETED",
        "started_at": recorder.started_at,
        "completed_at": iso_now(),
        "environment": {
            "operating_system": platform.platform(),
            "python_version": platform.python_version(),
            "application_commit_sha": commit_sha,
            "application_tree_sha": tree_sha,
            "source_database": source_database,
            "restored_database": restored_database,
            "database_revision": {
                "alembic_head": restored_schema_head,
                "recorded_schema_migrations": restored_migration_count,
            },
            "source_session_security_epoch": 1,
            "restored_session_security_epoch": 2,
            "source_key_version": SOURCE_KEY_VERSION,
            "restored_current_key_version": ROTATED_KEY_VERSION,
            "restored_previous_key_version": SOURCE_KEY_VERSION,
            "transition_started_at": transition_started_at.isoformat(),
            "transition_expires_at": transition_expires_at.isoformat(),
        },
        "backup": {
            "started_at": backup_started_at,
            "completed_at": backup_completed_at,
            "artifact": str(backup_path.resolve()),
            "size_bytes": backup_path.stat().st_size,
            "sha256": backup_digest,
        },
        "checks": [asdict(check) for check in recorder.checks],
        "command_log": recorder.commands,
        "timestamp_log": recorder.events,
        "remaining_gaps": [check.criterion for check in recorder.checks if check.status != "PASS"],
        "retained_local_evidence": {
            "source_database": source_database,
            "restored_database": restored_database,
            "backup_artifact": str(backup_path.resolve()),
            "cleanup_performed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=REPOSITORY_ROOT / "tmp" / "f003-restore-evidence",
    )
    arguments = parser.parse_args()
    result = run_drill(arguments.output_directory)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["execution_status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
