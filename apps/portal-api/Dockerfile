FROM python:3.11.15-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY apps/portal-api/pyproject.toml ./
COPY apps/portal-api/app ./app
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11.15-slim-bookworm AS runtime

ARG PORTAL_API_VERSION=0.1.0-dev
ARG PORTAL_API_BUILD_SHA=local
ARG PORTAL_API_BUILD_TIME=local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORTAL_API_SERVICE_VERSION=${PORTAL_API_VERSION} \
    PORTAL_API_BUILD_SHA=${PORTAL_API_BUILD_SHA} \
    PORTAL_API_BUILD_TIME=${PORTAL_API_BUILD_TIME} \
    PORTAL_API_HOST=0.0.0.0 \
    PORTAL_API_PORT=8010

RUN addgroup --system --gid 10001 portal \
    && adduser --system --uid 10001 --ingroup portal --home /home/portal portal

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

USER 10001:10001
WORKDIR /app
EXPOSE 8010

HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health/live', timeout=2)"]

CMD ["uvicorn", "portal_api.main:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
