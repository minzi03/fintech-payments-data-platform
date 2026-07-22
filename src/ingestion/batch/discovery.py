"""Settlement file discovery, naming validation, and stable content identity."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

from .models import FilenameMetadata

FILE_ID_NAMESPACE = UUID("22a08d63-cffc-4d36-8a83-a7b0d31c9f16")


class DiscoveryError(ValueError):
    """Raised when file selection or file naming is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def discover_files(*, file: Path | None, input_dir: Path | None) -> tuple[Path, ...]:
    """Return deterministic non-recursive file discoveries from one input mode."""
    if (file is None) == (input_dir is None):
        raise DiscoveryError("INVALID_INPUT_MODE", "Specify exactly one of --file or --input-dir")
    if file is not None:
        if not file.is_file():
            raise DiscoveryError("FILE_NOT_FOUND", f"Settlement file does not exist: {file}")
        return (file,)
    if input_dir is None or not input_dir.is_dir():
        raise DiscoveryError("INPUT_DIR_NOT_FOUND", f"Input directory does not exist: {input_dir}")
    return tuple(sorted(path for path in input_dir.iterdir() if path.is_file()))


def parse_settlement_filename(
    path: Path,
    naming_pattern: str,
    expected_partner_id: str | None = None,
) -> FilenameMetadata:
    """Parse and validate the partner, ISO date, and positive sequence from a file name."""
    if path.suffix != ".csv":
        raise DiscoveryError("INVALID_EXTENSION", "Settlement file extension must be .csv")
    match = re.fullmatch(naming_pattern, path.name)
    if match is None:
        raise DiscoveryError("INVALID_FILE_NAME", f"Invalid settlement file name: {path.name}")
    partner_id = match.group("partner_id")
    if expected_partner_id and partner_id != expected_partner_id:
        raise DiscoveryError(
            "PARTNER_FILE_NAME_MISMATCH",
            f"File partner {partner_id} does not match expected partner {expected_partner_id}",
        )
    try:
        settlement_date = date.fromisoformat(match.group("settlement_date"))
    except ValueError as error:
        raise DiscoveryError("INVALID_SETTLEMENT_DATE", "File name date is not valid") from error
    sequence = int(match.group("sequence"))
    if sequence < 1:
        raise DiscoveryError("INVALID_FILE_SEQUENCE", "Settlement file sequence must be 001-999")
    return FilenameMetadata(partner_id, settlement_date, sequence, path.name)


def calculate_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Calculate a streaming SHA-256 checksum without loading the full file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_file_id(source_name: str, partner_id: str, checksum_sha256: str) -> str:
    """Return a stable UUID for one source/partner/content identity."""
    identity = f"{source_name}:{partner_id}:{checksum_sha256}"
    return str(uuid5(FILE_ID_NAMESPACE, identity))
