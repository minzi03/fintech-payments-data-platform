# syntax=docker/dockerfile:1.23@sha256:2780b5c3bab67f1f76c781860de469442999ed1a0d7992a5efdf2cffc0e3d769

ARG SOURCE_DATE_EPOCH=0

FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY apps/portal-api/requirements.lock ./
RUN python -m pip wheel \
    --no-cache-dir \
    --only-binary=:all: \
    --require-hashes \
    --wheel-dir /wheels \
    --requirement requirements.lock

FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba AS runtime

ARG PORTAL_API_VERSION=0.1.0-dev
ARG PORTAL_API_BUILD_SHA=local
ARG PORTAL_API_BUILD_TIME=local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORTAL_API_SERVICE_VERSION=${PORTAL_API_VERSION} \
    PORTAL_API_BUILD_SHA=${PORTAL_API_BUILD_SHA} \
    PORTAL_API_BUILD_TIME=${PORTAL_API_BUILD_TIME} \
    PORTAL_API_HOST=0.0.0.0 \
    PORTAL_API_PORT=8010

RUN addgroup --system --gid 10001 portal \
    && adduser --system --uid 10001 --ingroup portal --home /home/portal portal

COPY --from=dependencies /wheels /wheels
RUN python -m pip install --no-cache-dir --no-compile --no-deps /wheels/* \
    && python -m pip uninstall --yes pip setuptools wheel \
    && rm -rf /wheels /root/.cache \
    && find /bin /sbin /usr/bin /usr/sbin -xdev -type f \
        \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} +
COPY apps/portal-api/app/portal_api /app/portal_api
COPY apps/portal-api/alembic.ini /app/alembic.ini
COPY apps/portal-api/migrations /app/migrations
RUN find /app -xdev -type d -exec chmod 0755 {} + \
    && find /app -xdev -type f -exec chmod 0644 {} +

USER 10001:10001
WORKDIR /app
EXPOSE 8010 9464

HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health/ready', timeout=2)"]

CMD ["uvicorn", "portal_api.main:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
