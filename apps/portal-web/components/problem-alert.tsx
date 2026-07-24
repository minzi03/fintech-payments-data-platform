"use client";

import { PortalApiError, safeErrorMessage } from "@/api/problem";

export function ProblemAlert({ error }: { error: unknown }) {
  const correlationId = error instanceof PortalApiError ? error.correlationId : null;
  return (
    <div className="problem-alert" role="alert" aria-live="assertive">
      <strong>Portal API unavailable</strong>
      <p>{safeErrorMessage(error)}</p>
      {correlationId ? (
        <p className="correlation">
          Correlation ID: <code>{correlationId}</code>
        </p>
      ) : null}
    </div>
  );
}
