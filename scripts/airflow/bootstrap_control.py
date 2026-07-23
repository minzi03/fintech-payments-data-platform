"""Create the dedicated orchestration control role and schema idempotently."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg import sql


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def main() -> None:
    host = _required("AIRFLOW_DATABASE_HOST")
    port = int(os.environ.get("AIRFLOW_DATABASE_PORT", "5432"))
    database = _required("AIRFLOW_DATABASE_NAME")
    admin_user = _required("AIRFLOW_DATABASE_USER")
    admin_password = _required("AIRFLOW_DATABASE_PASSWORD")
    control_user = _required("CONTROL_DATABASE_USER")
    control_password = _required("CONTROL_DATABASE_PASSWORD")
    schema_path = Path(
        os.environ.get(
            "CONTROL_SCHEMA_SQL",
            "/opt/airflow/project/infrastructure/airflow/init/001_create_control_schema.sql",
        )
    )

    with psycopg.connect(
        host=host,
        port=port,
        dbname=database,
        user=admin_user,
        password=admin_password,
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (control_user,))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(control_user),
                        sql.Literal(control_password),
                    )
                )
            else:
                cursor.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                        sql.Identifier(control_user),
                        sql.Literal(control_password),
                    )
                )

        connection.execute(schema_path.read_text(encoding="utf-8"))
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(control_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA control TO {}").format(sql.Identifier(control_user))
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA control TO {}"
                ).format(sql.Identifier(control_user))
            )
            cursor.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA control TO {}").format(
                    sql.Identifier(control_user)
                )
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA control "
                    "GRANT SELECT, INSERT, UPDATE ON TABLES TO {}"
                ).format(sql.Identifier(control_user))
            )

    print("Airflow control schema and least-privilege application role are ready.")


if __name__ == "__main__":
    main()
