import type { DependencyStatus, ReadinessStatus } from "@fintech/portal-contracts";

type Status = DependencyStatus | ReadinessStatus | "UP" | "CHECKING" | "ERROR";

const STATUS_LABELS: Record<Status, string> = {
  UP: "Up",
  READY: "Ready",
  DEGRADED: "Degraded",
  NOT_READY: "Not ready",
  UNAVAILABLE: "Unavailable",
  TIMEOUT: "Timed out",
  NOT_CONFIGURED: "Not configured",
  PLANNED: "Planned",
  CHECKING: "Checking",
  ERROR: "Error",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`status-badge status-${status.toLowerCase().replace("_", "-")}`}>
      <span aria-hidden="true" className="status-dot" />
      {STATUS_LABELS[status]}
    </span>
  );
}
