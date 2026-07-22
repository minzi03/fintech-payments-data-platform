"""CLI for settlement fixture generation and local Bronze batch ingestion."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from common.logging import configure_logging

from .contracts import ContractError, load_settlement_contract
from .discovery import DiscoveryError, discover_files
from .fixtures import FixtureConfig, generate_settlement_fixtures
from .manifest import ManifestStore
from .models import ManifestStatus
from .settlement_ingestor import SettlementIngestor
from .storage import LocalSettlementStorage

LOGGER = logging.getLogger(__name__)


def build_parser(environ: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    """Build the Phase 2 command tree with environment-backed storage defaults."""
    environment = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description="Banking partner settlement batch ingestion")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest-settlements", description="Validate and ingest settlement CSV files"
    )
    input_group = ingest.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--file", type=Path)
    input_group.add_argument("--input-dir", type=Path)
    ingest.add_argument("--partner-id", required=True)
    ingest.add_argument("--contract", required=True, type=Path)
    ingest.add_argument(
        "--bronze-dir",
        type=Path,
        default=Path(environment.get("SETTLEMENT_BRONZE_DIR", "data/bronze/settlements")),
    )
    ingest.add_argument(
        "--quarantine-dir",
        type=Path,
        default=Path(environment.get("SETTLEMENT_QUARANTINE_DIR", "data/quarantine/settlements")),
    )
    ingest.add_argument(
        "--manifest-db",
        type=Path,
        default=Path(
            environment.get("SETTLEMENT_MANIFEST_DB", "data/control/settlement_manifest.sqlite3")
        ),
    )
    ingest.add_argument("--dry-run", action="store_true")
    ingest.add_argument("--fail-on-rejected-records", action="store_true")

    fixtures = subparsers.add_parser(
        "generate-settlement-fixtures", description="Create deterministic settlement scenarios"
    )
    fixtures.add_argument(
        "--output-dir",
        type=Path,
        default=Path(environment.get("SETTLEMENT_INBOUND_DIR", "data/inbound/settlements")),
    )
    fixtures.add_argument("--partner-id", default="VCB")
    fixtures.add_argument("--settlement-date", type=date.fromisoformat, default=date(2026, 7, 22))
    fixtures.add_argument("--seed", type=int, default=42)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one batch command and return a truthful process status."""
    try:
        configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
        args = build_parser().parse_args(argv)
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", args.partner_id) is None:
            raise ValueError("partner_id must be 2-16 uppercase alphanumeric characters")
        if args.command == "generate-settlement-fixtures":
            paths = generate_settlement_fixtures(
                FixtureConfig(
                    output_dir=args.output_dir,
                    partner_id=args.partner_id,
                    settlement_date=args.settlement_date,
                    seed=args.seed,
                )
            )
            print(
                json.dumps(
                    {"generated": {name: str(path) for name, path in sorted(paths.items())}},
                    sort_keys=True,
                )
            )
            return 0

        contract = load_settlement_contract(args.contract)
        paths = discover_files(file=args.file, input_dir=args.input_dir)
        ingestor = SettlementIngestor(
            contract=contract,
            manifest=ManifestStore(args.manifest_db),
            storage=LocalSettlementStorage(args.bronze_dir, args.quarantine_dir),
        )
        results = ingestor.ingest_many(
            paths,
            expected_partner_id=args.partner_id,
            dry_run=args.dry_run,
            fail_on_rejected_records=args.fail_on_rejected_records,
        )
        for result in results:
            print(json.dumps(result.to_dict(), sort_keys=True))
        LOGGER.info(
            "Settlement batch completed files=%s processed=%s quarantined=%s failed=%s",
            len(results),
            sum(result.status is ManifestStatus.PROCESSED for result in results),
            sum(result.status is ManifestStatus.QUARANTINED for result in results),
            sum(result.status is ManifestStatus.FAILED for result in results),
        )
        return (
            1
            if any(
                result.status in {ManifestStatus.QUARANTINED, ManifestStatus.FAILED}
                for result in results
            )
            else 0
        )
    except (ContractError, DiscoveryError, OSError, ValueError) as error:
        LOGGER.error("Settlement command failed: %s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
