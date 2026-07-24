"""Portal API configuration safety tests."""

import pytest
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from pydantic import ValidationError


def test_local_defaults_are_explicit_and_safe() -> None:
    settings = PortalApiSettings(_env_file=None)

    assert settings.environment is PortalEnvironment.LOCAL
    assert settings.service_version == "0.1.0-dev"
    assert settings.build_sha == "local"
    assert settings.allowed_origin_values == ("http://localhost:3000",)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("allowed_origins", "*", "explicit HTTP"),
        ("allowed_origins", "http://localhost:3000/path", "must not contain paths"),
        ("log_format", "xml", "json or console"),
        ("api_version", "v2", "must be v1"),
    ],
)
def test_invalid_configuration_is_rejected(field: str, value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        PortalApiSettings(**{field: value})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"openapi_enabled": True}, "OPENAPI_ENABLED"),
        ({"log_format": "console"}, "LOG_FORMAT"),
        ({"development_identity_enabled": True}, "forbidden"),
        ({"trusted_hosts": "*"}, "Wildcard"),
        ({"allowed_origins": "http://portal.example"}, "HTTPS"),
        ({"build_sha": "local"}, "build SHA"),
    ],
)
def test_production_rejects_unsafe_defaults(overrides: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "environment": PortalEnvironment.PRODUCTION,
        "openapi_enabled": False,
        "log_format": "json",
        "development_identity_enabled": False,
        "trusted_hosts": "portal-api.example",
        "allowed_origins": "https://portal.example",
        "build_sha": "abc123",
        "build_time": "2026-07-24T00:00:00Z",
    }
    values.update(overrides)

    with pytest.raises(ValidationError, match=message):
        PortalApiSettings(**values)


def test_production_configuration_can_be_valid() -> None:
    settings = PortalApiSettings(
        environment=PortalEnvironment.PRODUCTION,
        openapi_enabled=False,
        log_format="json",
        trusted_hosts="portal-api.example",
        allowed_origins="https://portal.example",
        build_sha="abc123",
        build_time="2026-07-24T00:00:00Z",
    )

    assert settings.is_production
