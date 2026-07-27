"""Model C callback transaction-boundary and atomicity tests."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.auth.callback import CallbackOrchestrator
from portal_api.auth.login_intent import LoginInitiationService
from portal_api.auth.policy import LocalDevelopmentCallbackPolicy
from portal_api.auth.ports import (
    CallbackPolicyDecision,
    OidcProviderPort,
    PolicyOutcome,
    ProviderExchangeFailure,
    ProviderFailureKind,
    ProviderTokenSet,
    ResolvedPrincipal,
    ValidatedIdentity,
)
from portal_api.auth.principal import ConfiguredPrincipalResolver
from portal_api.auth.provider_config import OidcProviderConfig
from portal_api.auth.recovery import CallbackRecovery
from portal_api.auth.security_material import EphemeralSecurityMaterial
from portal_api.auth.session import SessionAuthenticationError, SessionService
from portal_api.auth.session_store import CallbackSessionStore, FinalizationRejected
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from starlette.responses import Response

TEST_SECURITY_MASTER_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
TEST_ROTATED_SECURITY_MASTER_KEY = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="


@pytest.fixture
def callback_database() -> Iterator[tuple[Engine, str]]:
    migration_url = os.environ.get("PORTAL_TEST_MIGRATION_DATABASE_URL", "")
    runtime_url = os.environ.get("PORTAL_TEST_RUNTIME_DATABASE_URL", "")
    if not migration_url or not runtime_url:
        pytest.skip("Disposable Portal security database is not configured")
    migration_engine = create_engine(migration_url)
    _clear_callback_state(migration_engine)
    try:
        yield migration_engine, runtime_url
    finally:
        _clear_callback_state(migration_engine)
        migration_engine.dispose()


def _clear_callback_state(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM portal_control.portal_token_envelopes"))
        connection.execute(text("DELETE FROM portal_control.portal_sessions"))
        connection.execute(text("DELETE FROM portal_control.portal_principals"))
        connection.execute(text("DELETE FROM portal_control.oidc_login_transactions"))
        connection.execute(text("DELETE FROM portal_control.portal_login_intents"))


def _settings(
    runtime_url: str,
    *,
    security_master_key: str = TEST_SECURITY_MASTER_KEY,
    security_key_version: str = "test-restart-v1",
    security_previous_master_key: str | None = None,
    security_previous_key_version: str | None = None,
    transition_started_at: datetime | None = None,
    transition_expires_at: datetime | None = None,
) -> PortalApiSettings:
    return PortalApiSettings(
        environment=PortalEnvironment.TEST,
        service_version="0.1.0-test",
        build_sha="test-sha",
        build_time="2026-07-27T00:00:00Z",
        log_level="WARNING",
        log_format="json",
        allowed_origins="http://portal.test",
        trusted_hosts="testserver,portal.test",
        security_runtime_enabled=True,
        database_url=runtime_url,
        security_master_key=security_master_key,
        security_key_version=security_key_version,
        security_previous_master_key=security_previous_master_key,
        security_previous_key_version=security_previous_key_version,
        security_key_transition_started_at=transition_started_at,
        security_key_transition_expires_at=transition_expires_at,
        oidc_issuer="http://identity.test/realms/portal",
        oidc_client_secret="test-client-secret",
        oidc_redirect_uri="http://portal.test/portal-api/v1/auth/callback",
    )


class TransactionBoundaryProvider:
    def __init__(self, inspection_engine: Engine) -> None:
        self._inspection_engine = inspection_engine
        self.calls = 0

    async def get_config(self, *, force_refresh: bool = False) -> OidcProviderConfig:
        del force_refresh
        return OidcProviderConfig(
            provider_id="local-keycloak",
            issuer="http://identity.test/realms/portal",
            client_id="fintech-portal",
            authorization_endpoint="http://identity.test/authorize",
            token_endpoint="http://identity.test/token",
            jwks_uri="http://identity.test/jwks",
            redirect_uri="http://portal.test/portal-api/v1/auth/callback",
            scopes=("openid", "profile"),
        )

    async def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet:
        assert code == "provider-code"
        assert verifier
        assert redirect_uri == "http://portal.test/portal-api/v1/auth/callback"
        with self._inspection_engine.begin() as connection:
            status = connection.execute(
                text(
                    "SELECT status FROM portal_control.oidc_login_transactions "
                    "WHERE status = 'CLAIMED' FOR UPDATE NOWAIT"
                )
            ).scalar_one()
        assert status == "CLAIMED"
        self.calls += 1
        return ProviderTokenSet(
            id_token="server-only-id-token",
            access_token="server-only-access-token",
            refresh_token="server-only-refresh-token",
            token_type="Bearer",
            expires_in=300,
        )

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, object]:
        raise AssertionError("The fake validator does not fetch JWKS")


class FailedExchangeProvider(TransactionBoundaryProvider):
    def __init__(self, inspection_engine: Engine, kind: ProviderFailureKind) -> None:
        super().__init__(inspection_engine)
        self._kind = kind

    async def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet:
        await super().exchange_code(
            code=code,
            verifier=verifier,
            redirect_uri=redirect_uri,
        )
        raise ProviderExchangeFailure(self._kind)


class FixedTokenValidator:
    async def validate(
        self,
        *,
        id_token: str,
        expected_nonce_hash: bytes,
    ) -> ValidatedIdentity:
        assert id_token == "server-only-id-token"
        assert expected_nonce_hash
        now = datetime.now(UTC)
        return ValidatedIdentity(
            issuer="http://identity.test/realms/portal",
            subject="subject-123",
            nonce="validated-by-port",
            groups=(
                "portal_role:portal_viewer",
                "portal_env:local",
                "unmapped-group",
            ),
            display_name="Portal User",
            assurance="AAL1",
            authenticated_at=now,
            token_expires_at=now + timedelta(minutes=5),
        )


class DenyPolicy:
    def evaluate(self, principal: ResolvedPrincipal) -> CallbackPolicyDecision:
        return CallbackPolicyDecision(
            outcome=PolicyOutcome.DENY,
            reason_code="TEST_DENY",
            policy_revision="test-policy-v1",
            capability_revision="test-capability-v1",
        )


class FailingSuccessAuditLedger(AuditLedger):
    def append(self, connection: Connection, event: AuditEvent) -> int:
        if event.event_type is AuditEventType.LOGIN_SUCCEEDED:
            raise RuntimeError("injected final audit failure")
        return super().append(connection, event)


def _replace_orchestrator(
    app,
    *,
    settings: PortalApiSettings,
    inspection_engine: Engine,
    policy=None,
    final_audit: AuditLedger | None = None,
    provider: OidcProviderPort | None = None,
) -> TransactionBoundaryProvider:
    runtime_engine = app.state.database_engine
    material = app.state.security_material
    cipher = app.state.protected_value_cipher
    assert isinstance(runtime_engine, Engine)
    assert isinstance(material, EphemeralSecurityMaterial)
    assert cipher is not None
    selected_provider = provider or TransactionBoundaryProvider(inspection_engine)
    selected_policy = policy or LocalDevelopmentCallbackPolicy(settings)
    app.state.login_initiation_service = LoginInitiationService(
        engine=runtime_engine,
        settings=settings,
        security_material=material,
        protected_value_cipher=cipher,
        provider=selected_provider,
    )
    app.state.callback_orchestrator = CallbackOrchestrator(
        engine=runtime_engine,
        settings=settings,
        security_material=material,
        protected_value_cipher=cipher,
        provider=selected_provider,
        token_validator=FixedTokenValidator(),
        principal_resolver=ConfiguredPrincipalResolver(
            engine=runtime_engine,
            settings=settings,
        ),
        policy=selected_policy,
        session_store=CallbackSessionStore(
            engine=runtime_engine,
            settings=settings,
            security_material=material,
            protected_value_cipher=cipher,
            audit_ledger=final_audit,
        ),
    )
    assert isinstance(selected_provider, TransactionBoundaryProvider)
    return selected_provider


def _begin_login(client: TestClient) -> str:
    context = client.get("/v1/auth/login-context").json()
    response = client.post(
        "/v1/auth/login",
        headers={"Origin": "http://portal.test"},
        json={"intent_token": context["intent_token"]},
    )
    assert response.status_code == 303, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def _complete_login(client: TestClient) -> object:
    state = _begin_login(client)
    response = client.get(
        "/v1/auth/callback",
        params={"state": state, "code": "provider-code"},
    )
    assert response.status_code == 303, response.text
    return response


@pytest.mark.integration
def test_session_and_pending_callback_survive_process_restart(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    first_app = create_app(settings=settings)
    _replace_orchestrator(
        first_app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(first_app, follow_redirects=False) as first_client:
        authenticated = _complete_login(first_client)
        session_secret = authenticated.cookies.get("fintech_portal_session_v1")
        pending_state = _begin_login(first_client)
        binding_secret = first_client.cookies.get("fintech_portal_oidc_binding_v1")
        assert session_secret
        assert binding_secret

    restarted_app = create_app(settings=settings)
    _replace_orchestrator(
        restarted_app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(restarted_app, follow_redirects=False) as restarted_client:
        restarted_client.cookies.set("fintech_portal_session_v1", session_secret)
        restored_session = restarted_client.get("/v1/session")
        restarted_client.cookies.set(
            "fintech_portal_oidc_binding_v1",
            binding_secret,
        )
        restored_callback = restarted_client.get(
            "/v1/auth/callback",
            params={"state": pending_state, "code": "provider-code"},
        )

    assert restored_session.status_code == 200
    assert restored_callback.status_code == 303


@pytest.mark.integration
def test_restart_during_and_after_controlled_key_transition_is_deterministic(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    original_settings = _settings(runtime_url)
    original_app = create_app(settings=original_settings)
    _replace_orchestrator(
        original_app,
        settings=original_settings,
        inspection_engine=migration_engine,
    )
    with TestClient(original_app, follow_redirects=False) as original_client:
        authenticated = _complete_login(original_client)
        original_session = authenticated.cookies.get("fintech_portal_session_v1")
        transition_intent = original_client.get("/v1/auth/login-context").json()["intent_token"]
        expired_intent = original_client.get("/v1/auth/login-context").json()["intent_token"]
        transition_state = _begin_login(original_client)
        transition_binding = original_client.cookies.get("fintech_portal_oidc_binding_v1")
        expired_state = _begin_login(original_client)
        expired_binding = original_client.cookies.get("fintech_portal_oidc_binding_v1")
        assert original_session
        assert transition_binding
        assert expired_binding

    now = datetime.now(UTC)
    transition_started_at = now - timedelta(minutes=1)
    transition_expires_at = now + timedelta(minutes=5)
    transition_settings = _settings(
        runtime_url,
        security_master_key=TEST_ROTATED_SECURITY_MASTER_KEY,
        security_key_version="test-restart-v2",
        security_previous_master_key=TEST_SECURITY_MASTER_KEY,
        security_previous_key_version="test-restart-v1",
        transition_started_at=transition_started_at,
        transition_expires_at=transition_expires_at,
    )
    transition_app = create_app(settings=transition_settings)
    _replace_orchestrator(
        transition_app,
        settings=transition_settings,
        inspection_engine=migration_engine,
    )
    with TestClient(transition_app, follow_redirects=False) as transition_client:
        restored_intent = transition_client.post(
            "/v1/auth/login",
            headers={"Origin": "http://portal.test"},
            json={"intent_token": transition_intent},
        )
        transition_client.cookies.set("fintech_portal_session_v1", original_session)
        restored_session = transition_client.get("/v1/session")
        restored_csrf = transition_client.get("/v1/session/csrf")
        previous_key_csrf = transition_client.post(
            "/v1/session/environment",
            headers={
                "Origin": "http://portal.test",
                "X-CSRF-Token": restored_csrf.json()["csrf_token"],
            },
            json={"environment_id": "local"},
        )
        transition_client.cookies.set(
            "fintech_portal_oidc_binding_v1",
            transition_binding,
        )
        restored_callback = transition_client.get(
            "/v1/auth/callback",
            params={"state": transition_state, "code": "provider-code"},
        )
        transition_client.cookies.clear()
        rotated_state = _begin_login(transition_client)
        rotated_login = transition_client.get(
            "/v1/auth/callback",
            params={"state": rotated_state, "code": "provider-code"},
        )
        rotated_session = rotated_login.cookies.get("fintech_portal_session_v1")

    assert restored_intent.status_code == 303
    assert restored_session.status_code == 200
    assert restored_csrf.status_code == 200
    assert previous_key_csrf.status_code == 200
    assert restored_callback.status_code == 303
    if rotated_login.status_code != 303:
        with migration_engine.connect() as connection:
            failure_reason = connection.execute(
                text(
                    "SELECT reason_code FROM portal_control.security_audit_events "
                    "WHERE correlation_id = :correlation_id "
                    "AND outcome = 'DENIED' ORDER BY ledger_sequence DESC LIMIT 1"
                ),
                {"correlation_id": rotated_login.headers["X-Correlation-ID"]},
            ).scalar_one_or_none()
        pytest.fail(f"Rotated-key login failed: {failure_reason}")
    assert rotated_session

    rollback_settings = _settings(
        runtime_url,
        security_master_key=TEST_SECURITY_MASTER_KEY,
        security_key_version="test-restart-v1",
        security_previous_master_key=TEST_ROTATED_SECURITY_MASTER_KEY,
        security_previous_key_version="test-restart-v2",
        transition_started_at=transition_started_at,
        transition_expires_at=transition_expires_at,
    )
    rollback_app = create_app(settings=rollback_settings)
    _replace_orchestrator(
        rollback_app,
        settings=rollback_settings,
        inspection_engine=migration_engine,
    )
    with TestClient(rollback_app, follow_redirects=False) as rollback_client:
        rollback_client.cookies.set("fintech_portal_session_v1", rotated_session)
        rollback_session = rollback_client.get("/v1/session")
    assert rollback_session.status_code == 200

    expired_started_at = now - timedelta(minutes=10)
    expired_at = now - timedelta(minutes=5)
    expired_settings = _settings(
        runtime_url,
        security_master_key=TEST_ROTATED_SECURITY_MASTER_KEY,
        security_key_version="test-restart-v2",
        security_previous_master_key=TEST_SECURITY_MASTER_KEY,
        security_previous_key_version="test-restart-v1",
        transition_started_at=expired_started_at,
        transition_expires_at=expired_at,
    )
    expired_app = create_app(settings=expired_settings)
    _replace_orchestrator(
        expired_app,
        settings=expired_settings,
        inspection_engine=migration_engine,
    )
    with TestClient(expired_app, follow_redirects=False) as expired_client:
        rejected_intent = expired_client.post(
            "/v1/auth/login",
            headers={"Origin": "http://portal.test"},
            json={"intent_token": expired_intent},
        )
        expired_client.cookies.set("fintech_portal_session_v1", original_session)
        rejected_session = expired_client.get("/v1/session")
        expired_client.cookies.set(
            "fintech_portal_oidc_binding_v1",
            expired_binding,
        )
        rejected_callback = expired_client.get(
            "/v1/auth/callback",
            params={"state": expired_state, "code": "provider-code"},
        )

    assert rejected_intent.status_code == 400
    assert rejected_session.status_code == 401
    assert rejected_callback.status_code == 401


@pytest.mark.integration
def test_callback_exchange_is_outside_database_transaction_and_success_is_atomic(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    provider = _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(app, follow_redirects=False) as client:
        state = _begin_login(client)
        response = client.get(
            "/v1/auth/callback",
            params={"state": state, "code": "provider-code"},
        )
        duplicate = client.get(
            "/v1/auth/callback",
            params={"state": state, "code": "provider-code"},
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "fintech_portal_session_v1=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "server-only" not in response.headers["set-cookie"]
    assert duplicate.status_code == 401
    assert provider.calls == 1
    with migration_engine.connect() as connection:
        transaction_status = connection.execute(
            text("SELECT status FROM portal_control.oidc_login_transactions")
        ).scalar_one()
        session = connection.execute(
            text(
                "SELECT status, roles_snapshot, environments_snapshot "
                "FROM portal_control.portal_sessions"
            )
        ).one()
        envelope = connection.execute(
            text("SELECT ciphertext, authentication_tag FROM portal_control.portal_token_envelopes")
        ).one()
        event_types = set(
            connection.execute(
                text(
                    "SELECT event_type FROM portal_control.security_audit_events "
                    "WHERE correlation_id = :correlation_id"
                ),
                {"correlation_id": response.headers["X-Correlation-ID"]},
            ).scalars()
        )
    assert transaction_status == "CONSUMED"
    assert session.status == "ACTIVE"
    assert session.roles_snapshot == ["portal_viewer"]
    assert session.environments_snapshot == ["local"]
    assert b"server-only" not in envelope.ciphertext + envelope.authentication_tag
    assert {
        "auth.login_started.v1",
        "auth.login_succeeded.v1",
        "auth.session_created.v1",
    }.issubset(event_types)


@pytest.mark.integration
def test_non_allow_policy_consumes_failure_without_creating_session(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
        policy=DenyPolicy(),
    )
    with TestClient(app, follow_redirects=False) as client:
        state = _begin_login(client)
        response = client.get(
            "/v1/auth/callback",
            params={"state": state, "code": "provider-code"},
        )

    assert response.status_code == 403
    assert "set-cookie" in response.headers
    assert "fintech_portal_session_v1=" not in response.headers["set-cookie"]
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM portal_control.oidc_login_transactions")
            ).scalar_one()
            == "CONSUMED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM portal_control.portal_sessions")
            ).scalar_one()
            == 0
        )


@pytest.mark.integration
def test_final_audit_failure_rolls_back_session_and_records_failed_consumption(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
        final_audit=FailingSuccessAuditLedger(),
    )
    with TestClient(app, follow_redirects=False, raise_server_exceptions=False) as client:
        state = _begin_login(client)
        response = client.get(
            "/v1/auth/callback",
            params={"state": state, "code": "provider-code"},
        )

    assert response.status_code == 503
    assert "fintech_portal_session_v1=" not in response.headers.get("set-cookie", "")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM portal_control.oidc_login_transactions")
            ).scalar_one()
            == "CONSUMED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM portal_control.portal_sessions")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM portal_control.security_audit_events "
                    "WHERE reason_code = 'CALLBACK_PROCESSING_ERROR' "
                    "AND correlation_id = :correlation_id"
                ),
                {"correlation_id": response.json()["correlation_id"]},
            ).scalar_one()
            == 1
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [
        (ProviderFailureKind.PRE_DISPATCH, "INVALIDATED"),
        (ProviderFailureKind.AUTHORITATIVE_REJECTION, "CONSUMED"),
        (ProviderFailureKind.AMBIGUOUS, "CONSUMED"),
    ],
)
def test_provider_failure_taxonomy_has_one_terminal_state(
    callback_database: tuple[Engine, str],
    kind: ProviderFailureKind,
    expected_status: str,
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    provider = FailedExchangeProvider(migration_engine, kind)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
        provider=provider,
    )
    with TestClient(app, follow_redirects=False) as client:
        state = _begin_login(client)
        response = client.get(
            "/v1/auth/callback",
            params={"state": state, "code": "provider-code"},
        )

    assert response.status_code == 503
    assert provider.calls == 1
    assert "fintech_portal_session_v1=" not in response.headers.get("set-cookie", "")
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM portal_control.oidc_login_transactions")
            ).scalar_one()
            == expected_status
        )


@pytest.mark.integration
def test_provider_error_and_browser_binding_mismatch_fail_before_exchange(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)

    provider_error_app = create_app(settings=settings)
    provider_error_adapter = _replace_orchestrator(
        provider_error_app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(provider_error_app, follow_redirects=False) as client:
        state = _begin_login(client)
        provider_error_response = client.get(
            "/v1/auth/callback",
            params={"state": state, "error": "access_denied"},
        )
    assert provider_error_response.status_code == 401
    assert provider_error_adapter.calls == 0
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM portal_control.oidc_login_transactions")
            ).scalar_one()
            == "INVALIDATED"
        )

    _clear_callback_state(migration_engine)
    binding_app = create_app(settings=settings)
    binding_adapter = _replace_orchestrator(
        binding_app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(binding_app, follow_redirects=False) as client:
        state = _begin_login(client)
        client.cookies.clear()
        client.cookies.set("fintech_portal_oidc_binding_v1", "tampered")
        binding_response = client.get(
            "/v1/auth/callback",
            params={"state": state, "code": "provider-code"},
        )
    assert binding_response.status_code == 401
    assert binding_adapter.calls == 0
    assert "Max-Age=0" in binding_response.headers["set-cookie"]
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM portal_control.oidc_login_transactions")
            ).scalar_one()
            == "INVALIDATED"
        )


@pytest.mark.integration
def test_expired_ambiguous_claim_is_conservatively_consumed(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(app, follow_redirects=False) as client:
        _begin_login(client)
    with migration_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE portal_control.oidc_login_transactions "
                "SET status = 'CLAIMED', claimed_by = :claim_id, "
                "claimed_at = CURRENT_TIMESTAMP - INTERVAL '10 minutes', "
                "expires_at = CURRENT_TIMESTAMP - INTERVAL '5 minutes', "
                "version = version + 1"
            ),
            {"claim_id": str(uuid4())},
        )
    runtime_engine = create_engine(runtime_url)
    try:
        recovered = CallbackRecovery(engine=runtime_engine).recover_expired_claims()
    finally:
        runtime_engine.dispose()

    assert recovered == 1
    with migration_engine.connect() as connection:
        transaction = connection.execute(
            text("SELECT status, consumed_at FROM portal_control.oidc_login_transactions")
        ).one()
        evidence = connection.execute(
            text(
                "SELECT safe_metadata FROM portal_control.security_audit_events "
                "WHERE reason_code = 'OIDC_STALE_CLAIM_AMBIGUOUS' "
                "ORDER BY ledger_sequence DESC LIMIT 1"
            )
        ).scalar_one()
    assert transaction.status == "CONSUMED"
    assert transaction.consumed_at is not None
    assert evidence["recovery_basis"] == "durable_expiry"


@pytest.mark.integration
def test_browser_binding_cleanup_failure_cannot_undo_committed_success(
    callback_database: tuple[Engine, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )

    def fail_cleanup(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(Response, "delete_cookie", fail_cleanup)
    with TestClient(app, follow_redirects=False) as client:
        state = _begin_login(client)
        response = client.get(
            "/v1/auth/callback",
            params={"state": state, "code": "provider-code"},
        )

    assert response.status_code == 303
    assert "fintech_portal_session_v1=" in response.headers["set-cookie"]
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM portal_control.oidc_login_transactions")
            ).scalar_one()
            == "CONSUMED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM portal_control.portal_sessions")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM portal_control.security_audit_events "
                    "WHERE event_type IN "
                    "('auth.login_succeeded.v1', 'auth.session_created.v1') "
                    "AND correlation_id = :correlation_id"
                ),
                {"correlation_id": response.headers["X-Correlation-ID"]},
            ).scalar_one()
            == 2
        )


@pytest.mark.integration
def test_session_csrf_environment_rotation_and_logout_lifecycle(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(app, follow_redirects=False) as client:
        callback = _complete_login(client)
        original_secret = callback.cookies.get("fintech_portal_session_v1")
        assert original_secret

        session_response = client.get("/v1/session")
        csrf_response = client.get("/v1/session/csrf")
        csrf_token = csrf_response.json()["csrf_token"]
        missing_csrf = client.post(
            "/v1/session/environment",
            headers={"Origin": "http://portal.test"},
            json={"environment_id": "local"},
        )
        wrong_origin = client.post(
            "/v1/session/environment",
            headers={
                "Origin": "http://attacker.test",
                "X-CSRF-Token": csrf_token,
            },
            json={"environment_id": "local"},
        )
        selected = client.post(
            "/v1/session/environment",
            headers={
                "Origin": "http://portal.test",
                "X-CSRF-Token": csrf_token,
            },
            json={"environment_id": "local"},
        )
        capabilities = client.get("/v1/capabilities?environment_id=local")
        navigation = client.get("/v1/navigation?environment_id=local")
        dependencies = client.get("/v1/system/dependencies?environment_id=local")
        unauthorized_environment = client.get("/v1/capabilities?environment_id=development")
        refreshed = client.post(
            "/v1/session/refresh",
            headers={
                "Origin": "http://portal.test",
                "X-CSRF-Token": csrf_token,
            },
        )
        successor_secret = refreshed.cookies.get("fintech_portal_session_v1")
        assert successor_secret and successor_secret != original_secret

        refreshed_csrf = client.get("/v1/session/csrf").json()["csrf_token"]
        stale_csrf = client.post(
            "/v1/auth/logout",
            headers={
                "Origin": "http://portal.test",
                "X-CSRF-Token": csrf_token,
            },
        )
        logout = client.post(
            "/v1/auth/logout",
            headers={
                "Origin": "http://portal.test",
                "X-CSRF-Token": refreshed_csrf,
            },
        )
        after_logout = client.get("/v1/session")

    assert session_response.status_code == 200
    assert csrf_response.status_code == 200
    assert missing_csrf.status_code == 403
    assert wrong_origin.status_code == 403
    assert selected.status_code == 200
    assert capabilities.status_code == 200
    assert navigation.status_code == 200
    assert dependencies.status_code == 200
    assert unauthorized_environment.status_code == 403
    assert refreshed.status_code == 200
    assert stale_csrf.status_code == 403
    assert logout.status_code == 200
    assert logout.json()["revoked_session_count"] == 1
    assert after_logout.status_code == 401
    with migration_engine.connect() as connection:
        sessions = connection.execute(
            text(
                "SELECT session_id, predecessor_session_id, status, "
                "absolute_expires_at, csrf_generation "
                "FROM portal_control.portal_sessions ORDER BY created_at"
            )
        ).all()
        csrf_denials = connection.execute(
            text(
                "SELECT count(*) FROM portal_control.security_audit_events "
                "WHERE event_type = 'auth.csrf_rejected.v1'"
            )
        ).scalar_one()
        token_envelope_count = connection.execute(
            text("SELECT count(*) FROM portal_control.portal_token_envelopes")
        ).scalar_one()
    assert len(sessions) == 2
    assert sessions[0].status == "TERMINATED"
    assert sessions[1].status == "TERMINATED"
    assert sessions[1].predecessor_session_id == sessions[0].session_id
    assert sessions[1].absolute_expires_at == sessions[0].absolute_expires_at
    assert sessions[1].csrf_generation == sessions[0].csrf_generation + 1
    assert csrf_denials >= 3
    assert token_envelope_count == 0


@pytest.mark.integration
def test_security_epoch_mismatch_invalidates_session(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(app, follow_redirects=False) as client:
        _complete_login(client)
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE portal_control.portal_sessions SET security_epoch = security_epoch + 1"
                )
            )
        response = client.get("/v1/session")

    assert response.status_code == 401
    with migration_engine.connect() as connection:
        session = connection.execute(
            text("SELECT status, revoked_reason FROM portal_control.portal_sessions")
        ).one()
    assert session.status == "INVALID"
    assert session.revoked_reason == "SESSION_SECURITY_EPOCH_INVALID"


@pytest.mark.integration
def test_unknown_session_key_version_fails_closed(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(app, follow_redirects=False) as client:
        _complete_login(client)
        with migration_engine.begin() as connection:
            connection.execute(
                text("UPDATE portal_control.portal_sessions SET lookup_key_version = 'unknown-v9'")
            )
        response = client.get("/v1/session")

    assert response.status_code == 401


@pytest.mark.integration
def test_sixth_login_revokes_oldest_and_preserves_five_active_sessions(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    provider = _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    with TestClient(app, follow_redirects=False) as client:
        for _ in range(6):
            _complete_login(client)

    assert provider.calls == 6
    with migration_engine.connect() as connection:
        status_counts = dict(
            connection.execute(
                text("SELECT status, count(*) FROM portal_control.portal_sessions GROUP BY status")
            ).all()
        )
        revocation_reason = connection.execute(
            text(
                "SELECT revoked_reason FROM portal_control.portal_sessions "
                "WHERE status = 'TERMINATED'"
            )
        ).scalar_one()
    assert status_counts == {"ACTIVE": 5, "TERMINATED": 1}
    assert revocation_reason == "MAXIMUM_ACTIVE_SESSIONS"


@pytest.mark.integration
def test_concurrent_rotation_and_current_logout_leave_no_active_family_member(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    service = app.state.session_service
    assert isinstance(service, SessionService)

    with TestClient(app, follow_redirects=False) as client:
        login = _complete_login(client)
        session_secret = login.cookies.get("fintech_portal_session_v1")
        assert session_secret
        session = service.authenticate(
            session_secret=session_secret,
            correlation_id="rotation-logout-auth",
            request_id="rotation-logout-auth",
        )

        start = threading.Barrier(2)

        def rotate() -> str:
            start.wait()
            try:
                service.rotate(
                    session=session,
                    correlation_id="rotation-race",
                    request_id="rotation-race",
                )
            except SessionAuthenticationError:
                return "CONFLICT"
            return "ROTATED"

        def logout() -> int:
            start.wait()
            return service.revoke_current(
                session=session,
                correlation_id="logout-race",
                request_id="logout-race",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            rotation_future = executor.submit(rotate)
            logout_future = executor.submit(logout)
            rotation_result = rotation_future.result(timeout=10)
            revoked = logout_future.result(timeout=10)

    assert rotation_result in {"ROTATED", "CONFLICT"}
    assert revoked == 1
    with migration_engine.connect() as connection:
        active = connection.execute(
            text(
                "SELECT count(*) FROM portal_control.portal_sessions "
                "WHERE session_family_id = :family_id "
                "AND status IN ('ACTIVE', 'REFRESH_REQUIRED')"
            ),
            {"family_id": session.session_family_id},
        ).scalar_one()
    assert active == 0


@pytest.mark.integration
def test_logout_all_fence_rejects_or_revokes_concurrent_callback_session(
    callback_database: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = callback_database
    settings = _settings(runtime_url)
    app = create_app(settings=settings)
    _replace_orchestrator(
        app,
        settings=settings,
        inspection_engine=migration_engine,
    )
    service = app.state.session_service
    runtime_engine = app.state.database_engine
    material = app.state.security_material
    cipher = app.state.protected_value_cipher
    assert isinstance(service, SessionService)
    assert isinstance(runtime_engine, Engine)
    assert isinstance(material, EphemeralSecurityMaterial)
    assert cipher is not None

    with TestClient(app, follow_redirects=False) as client:
        login = _complete_login(client)
        session_secret = login.cookies.get("fintech_portal_session_v1")
        assert session_secret
        session = service.authenticate(
            session_secret=session_secret,
            correlation_id="callback-logout-auth",
            request_id="callback-logout-auth",
        )
        _begin_login(client)

        claim_id = uuid4()
        with migration_engine.begin() as connection:
            transaction_id = connection.execute(
                text(
                    "UPDATE portal_control.oidc_login_transactions "
                    "SET status = 'CLAIMED', claimed_by = :claim_id, "
                    "claimed_at = CURRENT_TIMESTAMP, version = version + 1 "
                    "WHERE status = 'PENDING' RETURNING transaction_id"
                ),
                {"claim_id": claim_id},
            ).scalar_one()
            principal_row = connection.execute(
                text(
                    "SELECT issuer, subject_reference "
                    "FROM portal_control.portal_principals "
                    "WHERE principal_id = :principal_id"
                ),
                {"principal_id": session.principal_id},
            ).one()

        now = datetime.now(UTC)
        principal = ResolvedPrincipal(
            principal_id=session.principal_id,
            issuer=principal_row.issuer,
            subject_reference=principal_row.subject_reference,
            display_attributes={"display_name": "Portal User"},
            status="ACTIVE",
            roles=session.roles,
            environment_ids=session.environment_ids,
            tenant_id=session.tenant_id,
            mapping_revision=session.mapping_revision,
            assurance=session.assurance,
            authenticated_at=now,
            token_expires_at=now + timedelta(minutes=5),
        )
        decision = CallbackPolicyDecision(
            outcome=PolicyOutcome.ALLOW,
            reason_code="TEST_ALLOW",
            policy_revision=session.policy_revision,
            capability_revision=session.capability_revision,
        )
        session_store = CallbackSessionStore(
            engine=runtime_engine,
            settings=settings,
            security_material=material,
            protected_value_cipher=cipher,
        )
        start = threading.Barrier(2)

        def finalize() -> str:
            start.wait()
            try:
                session_store.finalize(
                    transaction_id=transaction_id,
                    claim_id=claim_id,
                    principal=principal,
                    decision=decision,
                    token_set=ProviderTokenSet(
                        id_token="server-only-id-token",
                        access_token="server-only-access-token",
                        refresh_token="server-only-refresh-token",
                        token_type="Bearer",
                        expires_in=300,
                    ),
                    correlation_id="callback-race",
                    request_id="callback-race",
                )
            except FinalizationRejected:
                return "REJECTED"
            return "FINALIZED"

        def logout_all() -> int:
            start.wait()
            return service.revoke_all(
                session=session,
                correlation_id="logout-all-race",
                request_id="logout-all-race",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            callback_future = executor.submit(finalize)
            logout_future = executor.submit(logout_all)
            callback_result = callback_future.result(timeout=10)
            revoked = logout_future.result(timeout=10)

    assert callback_result in {"FINALIZED", "REJECTED"}
    assert revoked >= 1
    with migration_engine.connect() as connection:
        active = connection.execute(
            text(
                "SELECT count(*) FROM portal_control.portal_sessions "
                "WHERE principal_id = :principal_id "
                "AND status IN ('ACTIVE', 'REFRESH_REQUIRED')"
            ),
            {"principal_id": session.principal_id},
        ).scalar_one()
        fence = connection.execute(
            text(
                "SELECT sessions_valid_after FROM portal_control.portal_principals "
                "WHERE principal_id = :principal_id"
            ),
            {"principal_id": session.principal_id},
        ).scalar_one()
        envelopes = connection.execute(
            text("SELECT count(*) FROM portal_control.portal_token_envelopes")
        ).scalar_one()
    assert active == 0
    assert fence is not None
    assert envelopes == 0
