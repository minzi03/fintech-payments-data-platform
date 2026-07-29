"""Frozen login-context and login-initiation HTTP models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LoginContextView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_token: str
    selected_provider: str
    return_to: str | None
    expires_at: datetime


class LoginRequest(BaseModel):
    # Runtime inspects and rejects extras so the rejection can receive required audit evidence.
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={"additionalProperties": False},
    )

    intent_token: str | None = None
    return_to: str | None = None
