#!/bin/sh
set -eu

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${PORTAL_DB_MIGRATION_PASSWORD:?PORTAL_DB_MIGRATION_PASSWORD must be set}"
: "${PORTAL_DB_RUNTIME_PASSWORD:?PORTAL_DB_RUNTIME_PASSWORD must be set}"
: "${PORTAL_DB_AUDIT_PASSWORD:?PORTAL_DB_AUDIT_PASSWORD must be set}"
: "${PORTAL_DB_ARCHIVE_PASSWORD:?PORTAL_DB_ARCHIVE_PASSWORD must be set}"

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=migration_password="$PORTAL_DB_MIGRATION_PASSWORD" \
  --set=runtime_password="$PORTAL_DB_RUNTIME_PASSWORD" \
  --set=audit_password="$PORTAL_DB_AUDIT_PASSWORD" \
  --set=archive_password="$PORTAL_DB_ARCHIVE_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE portal_migration LOGIN PASSWORD %L', :'migration_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_migration')
\gexec

SELECT format('CREATE ROLE portal_runtime LOGIN PASSWORD %L', :'runtime_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_runtime')
\gexec

SELECT format('CREATE ROLE portal_audit LOGIN PASSWORD %L', :'audit_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_audit')
\gexec

SELECT format('CREATE ROLE portal_archive LOGIN PASSWORD %L', :'archive_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_archive')
\gexec

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format(
  'GRANT CONNECT, CREATE ON DATABASE %I TO portal_migration',
  current_database()
)
\gexec
SELECT format(
  'GRANT CONNECT ON DATABASE %I TO portal_runtime, portal_audit, portal_archive',
  current_database()
)
\gexec
SQL
