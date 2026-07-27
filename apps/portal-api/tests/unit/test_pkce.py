"""PKCE generation tests."""

from __future__ import annotations

import base64
import hashlib

from portal_api.auth.pkce import generate_pkce_pair


def test_pkce_pair_uses_s256_and_never_reuses_verifiers() -> None:
    first = generate_pkce_pair()
    second = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(first.verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )

    assert first.challenge == expected
    assert first.verifier != second.verifier
    assert first.challenge != second.challenge
    assert len(first.verifier) >= 43
