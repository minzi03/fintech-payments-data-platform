"""Delete the development connector with an explicit confirmation flag."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from common.config import ConfigurationError
from ingestion.cdc.config import CdcSettings
from ingestion.cdc.connect_api import ConnectApiError, ConnectClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete the local Debezium connector")
    parser.add_argument("--confirm", action="store_true", help="Confirm destructive deletion")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm:
        print(json.dumps({"status": "refused", "error": "Pass --confirm to delete connector"}))
        return 2
    try:
        settings = CdcSettings.from_env(os.environ)
        deleted = ConnectClient(settings).delete()
    except (ConfigurationError, ConnectApiError, OSError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 2
    print(
        json.dumps(
            {"name": settings.connector_name, "action": "deleted" if deleted else "not_found"},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
