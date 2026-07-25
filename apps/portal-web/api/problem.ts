import type { ProblemDetails } from "@fintech/portal-contracts";

export class PortalApiError extends Error {
  readonly correlationId: string | null;
  readonly retryable: boolean;
  readonly status: number | null;
  readonly problem: ProblemDetails | null;

  constructor(
    message: string,
    options: {
      correlationId?: string | null;
      retryable?: boolean;
      status?: number | null;
      problem?: ProblemDetails | null;
      cause?: unknown;
    } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = "PortalApiError";
    this.correlationId = options.correlationId ?? null;
    this.retryable = options.retryable ?? false;
    this.status = options.status ?? null;
    this.problem = options.problem ?? null;
  }
}

export function isProblemDetails(value: unknown): value is ProblemDetails {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.type === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.status === "number" &&
    typeof candidate.detail === "string" &&
    typeof candidate.error_code === "string" &&
    typeof candidate.correlation_id === "string"
  );
}

export function safeErrorMessage(error: unknown): string {
  if (error instanceof PortalApiError) {
    return error.message;
  }
  return "The Portal API could not be reached. No infrastructure operation was attempted.";
}
