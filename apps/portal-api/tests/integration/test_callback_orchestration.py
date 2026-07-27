"""Model C callback transaction-boundary and atomicity tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.auth.callback import CallbackOrchestrator
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
from portal_api.auth.recovery import CallbackRecovery
from portal_api.auth.security_material import EphemeralSecurityMaterial
from portal_api.auth.session_store import CallbackSessionStore
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from starlette.responses import Response


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


def _settings(runtime_url: str) -> PortalApiSettings:
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
        oidc_issuer="http://identity.test/realms/portal",
        oidc_authorization_endpoint="http://identity.test/authorize",
        oidc_token_endpoint="http://identity.test/token",
        oidc_jwks_uri="http://identity.test/jwks",
        oidc_redirect_uri="http://portal.test/portal-api/v1/auth/callback",
    )


class TransactionBoundaryProvider:
    def __init__(self, inspection_engine: Engine) -> None:
        self._inspection_engine = inspection_engine
        self.calls = 0

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
                text("SELECT status FROM portal_control.oidc_login_transactions FOR UPDATE NOWAIT")
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
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


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
