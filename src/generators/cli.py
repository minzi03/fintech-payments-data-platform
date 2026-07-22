"""Command-line entry point for one transactional generator iteration."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Mapping, Sequence

import psycopg

from common.config import ConfigurationError, DatabaseSettings
from common.database import database_connection
from common.logging import configure_logging
from generators.models import GeneratorConfig
from generators.payments_generator import PaymentsGenerator
from generators.repositories import PaymentRepository

LOGGER = logging.getLogger(__name__)


def build_parser(environ: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    """Build the Phase 1 CLI with environment-backed defaults."""
    environment = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(
        description="Generate deterministic fintech payment data in one database transaction."
    )
    parser.add_argument("--seed", type=int, default=environment.get("GENERATOR_SEED", "42"))
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one generation iteration; continuous mode is out of scope for Phase 1.",
    )
    parser.add_argument(
        "--customers",
        type=int,
        default=environment.get("GENERATOR_CUSTOMERS", "10"),
    )
    parser.add_argument(
        "--merchants",
        type=int,
        default=environment.get("GENERATOR_MERCHANTS", "5"),
    )
    parser.add_argument(
        "--transactions",
        type=int,
        default=environment.get("GENERATOR_TRANSACTIONS", "50"),
    )
    parser.add_argument(
        "--invalid-rate",
        type=float,
        default=environment.get("GENERATOR_INVALID_RATE", "0.0"),
    )
    parser.add_argument(
        "--duplicate-rate",
        type=float,
        default=environment.get("GENERATOR_DUPLICATE_RATE", "0.0"),
    )
    return parser


def parse_generator_config(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> GeneratorConfig:
    """Parse and validate deterministic generation controls."""
    parser = build_parser(environ)
    args = parser.parse_args(argv)
    if not args.once:
        parser.error("Phase 1 requires --once; continuous generation is not implemented")
    try:
        return GeneratorConfig(
            seed=args.seed,
            customers=args.customers,
            merchants=args.merchants,
            transactions=args.transactions,
            invalid_rate=args.invalid_rate,
            duplicate_rate=args.duplicate_rate,
        )
    except ValueError as error:
        parser.error(str(error))


def main(argv: Sequence[str] | None = None) -> int:
    """Run one atomic generation iteration and return a process status code."""
    environment = os.environ
    try:
        configure_logging(environment.get("LOG_LEVEL", "INFO"))
        generator_config = parse_generator_config(argv, environment)
        database_settings = DatabaseSettings.from_env(environment)
    except (ConfigurationError, ValueError) as error:
        logging.getLogger(__name__).error("Configuration error: %s", error)
        return 2

    LOGGER.info(
        "Starting seed=%s target=%s customers=%s merchants=%s transactions=%s",
        generator_config.seed,
        database_settings.connection_label,
        generator_config.customers,
        generator_config.merchants,
        generator_config.transactions,
    )

    dataset = PaymentsGenerator(generator_config).generate()
    try:
        with (
            database_connection(database_settings) as connection,
            connection.transaction(),
        ):
            summary = PaymentRepository(connection).persist(dataset)
    except (psycopg.Error, RuntimeError, ValueError):
        LOGGER.exception("Generator iteration failed; database transaction was rolled back")
        return 1

    LOGGER.info(
        "Committed customers=%s accounts=%s merchants=%s transactions=%s events=%s refunds=%s "
        "invalid_rejections=%s duplicate_rejections=%s",
        summary.customers,
        summary.accounts,
        summary.merchants,
        summary.transactions,
        summary.events,
        summary.refunds,
        summary.invalid_rejections,
        summary.duplicate_rejections,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
