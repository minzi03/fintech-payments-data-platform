"""Central logging configuration for local services and generators."""

from __future__ import annotations

import logging


def configure_logging(level: str) -> None:
    """Configure structured-enough console logs without connection secrets."""
    normalized_level = level.upper()
    numeric_level = getattr(logging, normalized_level, None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unsupported log level: {level}")

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
