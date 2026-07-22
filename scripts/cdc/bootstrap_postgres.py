"""Bootstrap the non-superuser CDC role and explicit PostgreSQL publication."""

from __future__ import annotations

import json
import os

import psycopg

from common.config import ConfigurationError
from ingestion.cdc.config import CdcSettings
from ingestion.cdc.postgres import PostgresAdminSettings, bootstrap_postgres_cdc


def main() -> int:
    try:
        result = bootstrap_postgres_cdc(
            PostgresAdminSettings.from_env(os.environ),
            CdcSettings.from_env(os.environ),
        )
    except (ConfigurationError, psycopg.Error, OSError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 2
    print(
        json.dumps(
            {
                "status": "ready",
                "role_created": result.role_created,
                "publication_created": result.publication_created,
                "publication_name": result.publication_name,
                "captured_table_count": result.captured_table_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
