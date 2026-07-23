BEGIN;

CREATE SCHEMA IF NOT EXISTS control;

CREATE TABLE IF NOT EXISTS control.pipeline_runs (
    pipeline_run_id UUID PRIMARY KEY,
    dag_id TEXT NOT NULL,
    airflow_run_id TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    logical_date TIMESTAMPTZ NOT NULL,
    run_type TEXT NOT NULL CHECK (run_type IN ('SCHEDULED', 'MANUAL', 'BACKFILL', 'RECOVERY')),
    status TEXT NOT NULL CHECK (
        status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'PARTIAL', 'SKIPPED')
    ),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    records_read BIGINT NOT NULL DEFAULT 0 CHECK (records_read >= 0),
    records_written BIGINT NOT NULL DEFAULT 0 CHECK (records_written >= 0),
    records_rejected BIGINT NOT NULL DEFAULT 0 CHECK (records_rejected >= 0),
    input_assets JSONB NOT NULL DEFAULT '[]'::jsonb,
    output_assets JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (dag_id, airflow_run_id)
);

CREATE TABLE IF NOT EXISTS control.task_runs (
    task_run_id UUID PRIMARY KEY,
    pipeline_run_id UUID NOT NULL REFERENCES control.pipeline_runs(pipeline_run_id),
    task_id TEXT NOT NULL,
    try_number INTEGER NOT NULL CHECK (try_number > 0),
    status TEXT NOT NULL CHECK (
        status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'PARTIAL', 'SKIPPED')
    ),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    records_read BIGINT NOT NULL DEFAULT 0 CHECK (records_read >= 0),
    records_written BIGINT NOT NULL DEFAULT 0 CHECK (records_written >= 0),
    records_rejected BIGINT NOT NULL DEFAULT 0 CHECK (records_rejected >= 0),
    result_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (pipeline_run_id, task_id, try_number)
);

CREATE TABLE IF NOT EXISTS control.data_quality_results (
    quality_result_id UUID PRIMARY KEY,
    pipeline_run_id UUID NOT NULL REFERENCES control.pipeline_runs(pipeline_run_id),
    rule_name TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('PASS', 'WARN', 'FAIL')),
    observed_value NUMERIC(20, 6),
    warn_threshold NUMERIC(20, 6),
    fail_threshold NUMERIC(20, 6),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (pipeline_run_id, rule_name)
);

CREATE TABLE IF NOT EXISTS control.backfill_requests (
    request_id UUID PRIMARY KEY,
    pipeline_name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('CDC', 'SETTLEMENT')),
    requested_by TEXT NOT NULL,
    entity_name TEXT,
    input_prefix TEXT,
    from_date DATE,
    to_date DATE,
    force_reprocess BOOLEAN NOT NULL DEFAULT FALSE,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL CHECK (
        status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'PARTIAL', 'SKIPPED')
    ),
    airflow_run_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_code TEXT,
    error_message TEXT,
    CHECK (from_date IS NULL OR to_date IS NULL OR from_date <= to_date)
);

CREATE TABLE IF NOT EXISTS control.asset_watermarks (
    pipeline_name TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    watermark_time TIMESTAMPTZ NOT NULL,
    source_reference TEXT,
    pipeline_run_id UUID REFERENCES control.pipeline_runs(pipeline_run_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (pipeline_name, asset_name)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status_started
    ON control.pipeline_runs (status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline_logical_date
    ON control.pipeline_runs (pipeline_name, logical_date DESC);
CREATE INDEX IF NOT EXISTS idx_task_runs_pipeline
    ON control.task_runs (pipeline_run_id, task_id);
CREATE INDEX IF NOT EXISTS idx_quality_pipeline_classification
    ON control.data_quality_results (pipeline_run_id, classification);
CREATE INDEX IF NOT EXISTS idx_backfill_status_created
    ON control.backfill_requests (status, created_at DESC);

COMMIT;
