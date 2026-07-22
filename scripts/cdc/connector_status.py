"""Inspect or restart the payments Debezium connector without exposing its config."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from common.config import ConfigurationError
from ingestion.cdc.config import CdcSettings
from ingestion.cdc.connect_api import ConnectApiError, ConnectClient, summarize_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show Debezium connector and task states")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--wait-running", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = CdcSettings.from_env(os.environ)
        client = ConnectClient(settings)
        if args.restart:
            client.restart()
        payload = client.wait_running() if args.wait_running or args.restart else client.status()
    except (ConfigurationError, ConnectApiError, OSError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 2
    print(json.dumps(summarize_status(payload), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
