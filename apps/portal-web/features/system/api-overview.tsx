"use client";

import Link from "next/link";

import { portalConfig } from "@/api/config";
import { ProblemAlert } from "@/components/problem-alert";
import { StatusBadge } from "@/components/status-badge";
import { useSystemHealth } from "@/features/system/use-system-health";

export function ApiOverview() {
  const { liveness, readiness, systemInfo } = useSystemHealth();
  const loading = liveness.isPending || readiness.isPending || systemInfo.isPending;
  const error = liveness.error ?? readiness.error ?? systemInfo.error;

  return (
    <section className="surface api-overview" aria-labelledby="connectivity-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Foundation connectivity</p>
          <h2 id="connectivity-title">Portal API</h2>
        </div>
        <StatusBadge
          status={
            loading
              ? "CHECKING"
              : error
                ? "ERROR"
                : (readiness.data?.status ?? liveness.data?.status ?? "ERROR")
          }
        />
      </div>
      {loading ? (
        <div className="loading-block" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          Checking the BFF boundary…
        </div>
      ) : error ? (
        <ProblemAlert error={error} />
      ) : (
        <dl className="metadata-grid">
          <div>
            <dt>Environment</dt>
            <dd>{systemInfo.data?.runtime_environment ?? portalConfig.environment}</dd>
          </div>
          <div>
            <dt>API version</dt>
            <dd>{systemInfo.data?.api_contract_version ?? "Unavailable"}</dd>
          </div>
          <div>
            <dt>Service build</dt>
            <dd>{systemInfo.data?.build_sha ?? "Unavailable"}</dd>
          </div>
          <div>
            <dt>Correlation ID</dt>
            <dd className="truncate">
              <code>{readiness.data?.correlation_id ?? "Unavailable"}</code>
            </dd>
          </div>
        </dl>
      )}
      <Link className="text-link" href="/system-status">
        Open System Status <span aria-hidden="true">→</span>
      </Link>
    </section>
  );
}
