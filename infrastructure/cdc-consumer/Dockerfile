FROM python:3.11.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && addgroup --system --gid 10001 cdc \
    && adduser --system --uid 10001 --ingroup cdc cdc \
    && mkdir -p /app/data/control /app/data/tmp/cdc_consumer \
    && chown -R cdc:cdc /app/data

USER 10001:10001

CMD ["python", "-m", "ingestion.cdc_consumer.cli", "run", "--storage-backend", "minio"]
