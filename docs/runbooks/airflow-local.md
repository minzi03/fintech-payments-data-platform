# Local Airflow Runbook

## Prepare configuration

Copy `.env.example` to ignored `.env`. Replace every `change_me`/`replace_with` Airflow, control,
PostgreSQL, and MinIO value. Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Generate `AIRFLOW_SECRET_KEY` with a password manager or `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
If a password contains URI-reserved characters, percent-encode it before Compose constructs the
connection URI.

## Cold start

Use a cold start after a fresh clone, an Airflow image/dependency change, or metadata-volume
creation:

```bash
make airflow-build
make airflow-init
make airflow-up
make airflow-dags-list
```

The UI/API is bound to `127.0.0.1:${AIRFLOW_WEB_PORT}`. SimpleAuthManager creates the local admin
password when the API server initializes a configured user and stores the username/password JSON
mapping at the configured `/opt/airflow/logs/simple_auth_passwords.json` path in the private
`airflow_logs` volume. No known default password is committed.

The screen-share-safe command prints only URL, username, and private retrieval instructions:

```bash
make airflow-demo-login-info
```

Retrieve the generated password only in a private terminal:

```bash
make airflow-show-demo-password CONFIRM=1
```

The guarded target reads only the configured password file and selected username. It does not scan
logs or print Fernet, JWT, database, or connection secrets. Close or clear that terminal before
sharing the screen, and do not save the output in notes, screenshots, or shell transcripts.

Airflow 3 runs `api-server`, scheduler, and the required DAG processor. Verify health with
`docker compose ps`; inspect bounded logs with `make airflow-logs`.

The one-shot `airflow-init` starts as root only to set ownership on named log/state/temp volumes,
then immediately uses `setpriv` to run migrations and control bootstrap as `AIRFLOW_UID`. The
API server, scheduler, and DAG processor always run as the non-root configured UID.

## Warm start

For an existing checkout and volumes, use Compose reconciliation rather than `docker compose
start`:

```bash
docker compose --env-file .env up -d
make cdc-status
make airflow-dags-list
```

`up -d` reuses compatible containers, creates missing services, and applies current Compose
configuration. It does not rebuild the custom Airflow image unless `--build` is supplied. Do not
rerun `airflow-init` during normal rehearsal unless metadata has not been initialized or a migration
is intentionally required.

## Stop and reset

`make airflow-down` removes only Airflow containers and retains named volumes. A destructive reset
requires `make reset-airflow-metadata CONFIRM=1`; it deletes only Airflow metadata/control, logs,
runtime auth/log state, and Airflow-owned component manifests. It does not delete payments PostgreSQL, Kafka,
MinIO Bronze/Silver objects, or CDC offsets.

## Security limitations

This topology is local development only: loopback UI, SimpleAuthManager, plaintext internal Docker
network connections, and one scheduler. Production requires TLS, external secrets, hardened auth,
RBAC review, backups, and HA sizing.
