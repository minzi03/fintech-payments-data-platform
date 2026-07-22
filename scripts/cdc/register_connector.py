"""Validate and idempotently create or update the payments PostgreSQL connector."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from common.config import ConfigurationError
from ingestion.cdc.config import CdcSettings, render_connector_definition
from ingestion.cdc.connect_api import ConnectApiError, ConnectClient, summarize_status

DEFAULT_TEMPLATE = Path("infrastructure/debezium/connectors/payments-postgres.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register the PostgreSQL Debezium connector")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--wait-running", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = CdcSettings.from_env(os.environ)
        definition = render_connector_definition(args.template, settings)
        client = ConnectClient(settings)
        client.wait_ready()
        action = client.ensure(definition)
        output: dict[str, object] = {"name": definition.name, "action": action.value}
        if args.wait_running:
            output["status"] = summarize_status(client.wait_running(definition.name))
    except (ConfigurationError, ConnectApiError, OSError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
