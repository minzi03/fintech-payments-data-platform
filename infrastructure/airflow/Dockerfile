FROM apache/airflow:3.3.0-python3.11

ARG AIRFLOW_VERSION=3.3.0

USER airflow
WORKDIR /opt/airflow/project

COPY --chown=airflow:root pyproject.toml README.md ./
COPY --chown=airflow:root src ./src
COPY --chown=airflow:root contracts ./contracts
COPY --chown=airflow:root scripts ./scripts
COPY --chown=airflow:root infrastructure/airflow/init ./infrastructure/airflow/init

# Reassert the base-image Airflow version so dependency resolution cannot
# silently replace it while installing the project package.
RUN pip install --no-cache-dir \
      "apache-airflow==${AIRFLOW_VERSION}" \
      "/opt/airflow/project" \
    && pip check

ENV PYTHONPATH=/opt/airflow/project/src
