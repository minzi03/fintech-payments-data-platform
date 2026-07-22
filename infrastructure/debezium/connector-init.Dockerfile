FROM python:3.11.9-slim-bookworm

RUN python -m pip install --no-cache-dir "psycopg[binary]==3.3.4"

WORKDIR /app
