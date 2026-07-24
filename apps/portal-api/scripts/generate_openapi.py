"""Generate the deterministic checked-in Portal API OpenAPI artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app


def generate_schema() -> dict[str, object]:
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        service_version="0.1.0",
        build_sha="contract",
        build_time="2026-07-24T00:00:00Z",
        log_level="WARNING",
        log_format="json",
        openapi_enabled=True,
    )
    return create_app(settings=settings).openapi()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(generate_schema(), indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
