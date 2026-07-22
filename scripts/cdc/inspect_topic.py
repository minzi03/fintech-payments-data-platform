"""Print bounded non-PII summaries from one CDC topic without committing offsets."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence

from common.config import ConfigurationError
from ingestion.cdc.config import CAPTURED_TABLES, CdcSettings
from ingestion.cdc.inspection import TopicInspectionError, consume_topic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect redacted Debezium CDC metadata")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--table", choices=CAPTURED_TABLES)
    source.add_argument("--topic")
    parser.add_argument("--max-messages", type=int, default=20)
    parser.add_argument("--timeout-ms", type=int, default=10_000)
    parser.add_argument("--compose-env", default=".env.example")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = CdcSettings.from_env(os.environ)
        topic = args.topic or settings.topic_name(args.table)
        records = consume_topic(
            topic=topic,
            max_messages=args.max_messages,
            timeout_ms=args.timeout_ms,
            compose_env=args.compose_env,
        )
    except (ConfigurationError, TopicInspectionError, OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 2
    for record in records:
        print(json.dumps(record.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
