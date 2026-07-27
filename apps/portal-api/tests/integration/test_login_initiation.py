"""Governed login-context and login-initiation integration tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent
from portal_api.auth.login_intent import LoginInitiationService
from portal_api.auth.protected_value import EphemeralEnvelopeCipher
from portal_api.auth.security_material import EphemeralSecurityMaterial
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine


@pytest.fixture
def portal_security_engines() -> Iterator[tuple[Engine, str]]:
    migration_url = os.environ.get("PORTAL_TEST_MIGRATION_DATABASE_URL", "")
    runtime_url = os.environ.get("PORTAL_TEST_RUNTIME_DATABASE_URL", "")
    if not migration_url or not runtime_url:
        pytest.skip("Disposable Portal security database is not configured")

    migration_engine = create_engine(migration_url)
    with migration_engine.begin() as connection:
        connection.execute(text("DELETE FROM portal_control.oidc_login_transactions"))
        connection.execute(text("DELETE FROM portal_control.portal_login_intents"))
    try:
        yield migration_engine, runtime_url
    finally:
        with migration_engine.begin() as connection:
            connection.execute(text("DELETE FROM portal_control.oidc_login_transactions"))
            connection.execute(text("DELETE FROM portal_control.portal_login_intents"))
        migration_engine.dispose()


def _secured_settings(runtime_url: str) -> PortalApiSettings:
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
        oidc_authorization_endpoint="http://identity.test/authorize",
        oidc_redirect_uri="http://portal.test/portal-api/v1/auth/callback",
    )


def _secured_client(runtime_url: str) -> TestClient:
    return TestClient(
        create_app(settings=_secured_settings(runtime_url)),
        follow_redirects=False,
    )


class FailingValidatedAuditLedger(AuditLedger):
    def append(self, connection: Connection, event: AuditEvent) -> int:
        if event.safe_metadata.get("login_intent_event") == "validated":
            raise RuntimeError("injected audit failure")
        return super().append(connection, event)


@pytest.mark.integration
def test_login_context_creates_one_use_server_owned_transaction(
    portal_security_engines: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = portal_security_engines
    with _secured_client(runtime_url) as client:
        context_response = client.get("/v1/auth/login-context?return_to=/system-status")
        assert context_response.status_code == 200
        context = context_response.json()
        assert context["selected_provider"] == "local-keycloak"
        assert context["return_to"] == "/system-status"
        assert "set-cookie" not in context_response.headers

        response = client.post(
            "/v1/auth/login",
            headers={"Origin": "http://portal.test"},
            json={
                "intent_token": context["intent_token"],
                "return_to": "/system-status",
            },
        )

    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    parameters = parse_qs(location.query)
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "http://identity.test/authorize"
    )
    assert parameters["response_type"] == ["code"]
    assert parameters["client_id"] == ["fintech-portal"]
    assert parameters["code_challenge_method"] == ["S256"]
    assert "code_verifier" not in parameters
    assert "token" not in response.headers["location"].lower()
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Domain=" not in cookie

    raw_state = parameters["state"][0]
    with migration_engine.connect() as connection:
        intent = connection.execute(
            text("SELECT status, consumed_at FROM portal_control.portal_login_intents")
        ).one()
        transaction = connection.execute(
            text(
                "SELECT state_hash, status, return_path, pkce_verifier_encrypted "
                "FROM portal_control.oidc_login_transactions"
            )
        ).one()
    assert intent.status == "CONSUMED"
    assert intent.consumed_at is not None
    assert transaction.status == "PENDING"
    assert transaction.return_path == "/system-status"
    assert transaction.state_hash != raw_state.encode("ascii")
    assert set(transaction.pkce_verifier_encrypted) == {
        "ciphertext",
        "key_reference",
        "nonce",
        "wrapped_data_key",
        "wrapped_data_key_nonce",
    }


@pytest.mark.integration
def test_login_intent_replay_and_forbidden_browser_input_fail_closed(
    portal_security_engines: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = portal_security_engines
    with _secured_client(runtime_url) as client:
        context = client.get("/v1/auth/login-context").json()
        accepted = client.post(
            "/v1/auth/login",
            headers={"Origin": "http://portal.test"},
            json={"intent_token": context["intent_token"]},
        )
        replay = client.post(
            "/v1/auth/login",
            headers={"Origin": "http://portal.test"},
            json={"intent_token": context["intent_token"]},
        )
        forbidden = client.post(
            "/v1/auth/login",
            headers={"Origin": "http://portal.test"},
            json={
                "intent_token": context["intent_token"],
                "provider": "attacker-selected",
            },
        )

    assert accepted.status_code == 303
    assert replay.status_code == 400
    assert forbidden.status_code == 400
    assert "attacker-selected" not in replay.text
    with migration_engine.connect() as connection:
        transaction_count = connection.execute(
            text("SELECT count(*) FROM portal_control.oidc_login_transactions")
        ).scalar_one()
        reasons = (
            connection.execute(
                text(
                    "SELECT reason_code FROM portal_control.security_audit_events "
                    "WHERE reason_code IN "
                    "('LOGIN_INTENT_REPLAYED', 'LOGIN_INTENT_FORBIDDEN_FIELD')"
                )
            )
            .scalars()
            .all()
        )
    assert transaction_count == 1
    assert set(reasons) == {
        "LOGIN_INTENT_REPLAYED",
        "LOGIN_INTENT_FORBIDDEN_FIELD",
    }


@pytest.mark.integration
def test_audit_failure_rolls_back_intent_consumption_and_transaction_creation(
    portal_security_engines: tuple[Engine, str],
) -> None:
    migration_engine, runtime_url = portal_security_engines
    runtime_engine = create_engine(runtime_url)
    material = EphemeralSecurityMaterial.generate()
    cipher = EphemeralEnvelopeCipher()
    settings = _secured_settings(runtime_url)
    context_service = LoginInitiationService(
        engine=runtime_engine,
        settings=settings,
        security_material=material,
        protected_value_cipher=cipher,
    )
    context = context_service.create_context(
        requested_return_path=None,
        correlation_id="atomic-context",
        request_id="atomic-context-request",
    )
    failing_service = LoginInitiationService(
        engine=runtime_engine,
        settings=settings,
        security_material=material,
        protected_value_cipher=cipher,
        audit_ledger=FailingValidatedAuditLedger(),
    )

    try:
        with pytest.raises(RuntimeError, match="injected audit failure"):
            failing_service.start_login(
                intent_token=context.intent_token,
                requested_return_path=None,
                extra_fields=frozenset(),
                origin="http://portal.test",
                referer=None,
                correlation_id="atomic-start",
                request_id="atomic-start-request",
            )
    finally:
        runtime_engine.dispose()

    with migration_engine.connect() as connection:
        intent_status = connection.execute(
            text("SELECT status FROM portal_control.portal_login_intents")
        ).scalar_one()
        transaction_count = connection.execute(
            text("SELECT count(*) FROM portal_control.oidc_login_transactions")
        ).scalar_one()
    assert intent_status == "PENDING"
    assert transaction_count == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("headers", "body_update"),
    [
        ({}, {}),
        ({"Origin": "http://attacker.test"}, {}),
        ({"Origin": "http://portal.test"}, {"return_to": "/system-status"}),
    ],
)
def test_invalid_origin_or_changed_return_path_cannot_create_transaction(
    portal_security_engines: tuple[Engine, str],
    headers: dict[str, str],
    body_update: dict[str, str],
) -> None:
    migration_engine, runtime_url = portal_security_engines
    with _secured_client(runtime_url) as client:
        context = client.get("/v1/auth/login-context").json()
        body = {"intent_token": context["intent_token"], **body_update}
        response = client.post("/v1/auth/login", headers=headers, json=body)

    assert response.status_code in {400, 403}
    assert "set-cookie" not in response.headers
    with migration_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM portal_control.oidc_login_transactions")
            ).scalar_one()
            == 0
        )
