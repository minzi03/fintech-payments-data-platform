"""Server-owned OIDC login-transaction construction."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from portal_api.auth.pkce import generate_pkce_pair
from portal_api.auth.protected_value import ProtectedValueCipher
from portal_api.auth.provider_config import OidcProviderConfig
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.core.config import PortalApiSettings


@dataclass(frozen=True)
class PendingLoginTransaction:
    transaction_id: UUID
    state: str
    nonce: str
    code_challenge: str
    browser_binding_secret: str
    expires_at: datetime
    values: dict[str, object]


def build_pending_login_transaction(
    *,
    settings: PortalApiSettings,
    provider: OidcProviderConfig,
    security_material: EphemeralSecurityMaterial,
    protected_value_cipher: ProtectedValueCipher,
    return_path: str,
    now: datetime | None = None,
) -> PendingLoginTransaction:
    issued_at = now or datetime.now(UTC)
    transaction_id = uuid4()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    browser_binding_secret = secrets.token_urlsafe(32)
    pkce = generate_pkce_pair()
    protected_verifier = protected_value_cipher.encrypt(
        pkce.verifier.encode("ascii"),
        context=str(transaction_id).encode("ascii"),
    )
    expires_at = issued_at + timedelta(seconds=settings.login_transaction_ttl_seconds)
    values: dict[str, object] = {
        "transaction_id": transaction_id,
        "state_hash": security_material.protect(state, purpose=ProtectedPurpose.OIDC_STATE),
        "nonce_hash": security_material.protect(nonce, purpose=ProtectedPurpose.OIDC_NONCE),
        "browser_binding_hash": security_material.protect(
            browser_binding_secret,
            purpose=ProtectedPurpose.BROWSER_BINDING,
        ),
        "browser_binding_key_version": security_material.key_version,
        "pkce_verifier_encrypted": json.loads(protected_verifier.serialize()),
        "provider_id": provider.provider_id,
        "redirect_uri": provider.redirect_uri,
        "return_path": return_path,
        "status": "PENDING",
        "version": 1,
        "expires_at": expires_at,
        "created_at": issued_at,
        "updated_at": issued_at,
    }
    return PendingLoginTransaction(
        transaction_id=transaction_id,
        state=state,
        nonce=nonce,
        code_challenge=pkce.challenge,
        browser_binding_secret=browser_binding_secret,
        expires_at=expires_at,
        values=values,
    )
