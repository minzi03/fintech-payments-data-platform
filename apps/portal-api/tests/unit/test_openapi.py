"""Deterministic Portal API contract tests."""

import json

from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app


def test_openapi_is_deterministic_and_contains_problem_contract() -> None:
    settings = PortalApiSettings(environment=PortalEnvironment.TEST)
    app = create_app(settings=settings)

    first = json.dumps(app.openapi(), sort_keys=True, separators=(",", ":"))
    app.openapi_schema = None
    second = json.dumps(app.openapi(), sort_keys=True, separators=(",", ":"))
    schemas = app.openapi()["components"]["schemas"]

    assert first == second
    assert "ProblemDetails" in schemas
    assert set(schemas["ProblemDetails"]["required"]) >= {
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "error_code",
        "correlation_id",
        "timestamp",
    }
    assert schemas["ReadinessStatus"]["enum"] == ["READY", "DEGRADED", "NOT_READY"]
    assert schemas["DependencyStatus"]["enum"] == [
        "UP",
        "DEGRADED",
        "UNAVAILABLE",
        "TIMEOUT",
        "NOT_CONFIGURED",
        "PLANNED",
    ]
    assert app.openapi()["paths"]["/health/ready"]["get"]["responses"]["503"]["content"]
