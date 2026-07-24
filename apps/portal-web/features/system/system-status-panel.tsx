"use client";

import { useQueryClient } from "@tanstack/react-query";

import { portalQueryKeys } from "@/api/query-keys";
import { ProblemAlert } from "@/components/problem-alert";
import { StatusBadge } from "@/components/status-badge";
import { useSystemHealth } from "@/features/system/use-system-health";

export function SystemStatusPanel() {
  const queryClient = useQueryClient();
  const { dependencies, liveness, readiness } = useSystemHealth();
  const loading = liveness.isPending || readiness.isPending || dependencies.isPending;
  const error = liveness.error ?? readiness.error ?? dependencies.error;
  const lastChecked = [
    liveness.dataUpdatedAt,
    readiness.dataUpdatedAt,
    dependencies.dataUpdatedAt,
  ].reduce((latest, current) => Math.max(latest, current), 0);

  async function retry() {
    await queryClient.invalidateQueries({ queryKey: portalQueryKeys.all });
  }

  return (
    <div className="status-stack">
      <section className="surface" aria-labelledby="overall-status-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">BFF boundary</p>
            <h2 id="overall-status-title">Current state</h2>
          </div>
          <StatusBadge
            status={loading ? "CHECKING" : error ? "ERROR" : (readiness.data?.status ?? "ERROR")}
          />
        </div>
        {loading ? (
          <div className="loading-block" role="status" aria-live="polite">
            <span className="spinner" aria-hidden="true" />
            Collecting bounded health checks…
          </div>
        ) : error ? (
          <ProblemAlert error={error} />
        ) : (
          <>
            <p className="status-reason">{readiness.data?.reason}</p>
            <div className="status-summary">
              <div>
                <span>Liveness</span>
                <StatusBadge status={liveness.data?.status ?? "ERROR"} />
              </div>
              <div>
                <span>Readiness</span>
                <StatusBadge status={readiness.data?.status ?? "ERROR"} />
              </div>
            </div>
          </>
        )}
        <div className="status-actions">
          <button className="button" type="button" onClick={retry} disabled={loading}>
            Check again
          </button>
          <span>
            Last checked: {lastChecked ? new Date(lastChecked).toLocaleTimeString() : "Not yet"}
          </span>
        </div>
      </section>

      <section className="surface" aria-labelledby="dependencies-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Adapter registry</p>
            <h2 id="dependencies-title">Foundation dependencies</h2>
          </div>
          <span className="count-label">
            {dependencies.data?.dependencies.length ?? 0} configured
          </span>
        </div>
        {dependencies.data?.dependencies.length ? (
          <ul className="dependency-list">
            {dependencies.data.dependencies.map((dependency) => (
              <li key={dependency.dependency_id}>
                <div>
                  <strong>{dependency.display_name}</strong>
                  <span>{dependency.dependency_type}</span>
                </div>
                <div className="dependency-state">
                  <StatusBadge status={dependency.status} />
                  <span>{dependency.reason ?? "Health check completed."}</span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="empty-state">
            <strong>No infrastructure adapters are enabled.</strong>
            <p>
              This is intentional for PR-PORTAL-001. The Portal remains available without Kafka,
              Airflow, MinIO, or PostgreSQL.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
