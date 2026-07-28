"""Fail fast when Portal API environment configuration is unsafe."""

from __future__ import annotations

from portal_api.configuration import PortalProcessRole
from portal_api.core.config import PortalApiSettings


def main() -> int:
    settings = PortalApiSettings()
    runtime = settings.for_role(PortalProcessRole.API)
    print(
        f"Portal API configuration is valid for environment={settings.environment.value} "
        f"service={settings.service_name} role={runtime.role.value}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
