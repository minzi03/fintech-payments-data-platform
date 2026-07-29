"""RFC 7636 PKCE S256 generation."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class PkcePair:
    verifier: str
    challenge: str


def generate_pkce_pair() -> PkcePair:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(verifier=verifier, challenge=challenge)
