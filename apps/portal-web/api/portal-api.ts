import { Sdk } from "@fintech/portal-contracts";
import type {
  DependencyListResponse,
  LivenessResponse,
  ProblemDetails,
  ReadinessResponse,
  SystemInfoResponse,
} from "@fintech/portal-contracts";
import { createClient } from "@fintech/portal-contracts/client";

import { portalConfig } from "@/api/config";
import { isProblemDetails, PortalApiError } from "@/api/problem";
import { recordPortalTelemetry } from "@/telemetry/events";

const REQUEST_TIMEOUT_MS = 5_000;

function correlationId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `portal-${Date.now().toString(36)}`;
}

const generatedClient = createClient({
  baseUrl: portalConfig.apiBaseUrl,
  credentials: "same-origin",
  responseStyle: "fields",
  throwOnError: false,
});

generatedClient.interceptors.request.use((request) => {
  const headers = new Headers(request.headers);
  if (!headers.has("X-Correlation-ID")) {
    headers.set("X-Correlation-ID", correlationId());
  }
  return new Request(request, { headers });
});

const sdk = new Sdk({ client: generatedClient });

type ApiResult<T> = {
  data?: T;
  error?: unknown;
  response?: Response;
};

function asProblem(error: unknown): ProblemDetails | null {
  return isProblemDetails(error) ? error : null;
}

async function unwrap<T>(route: string, operation: () => Promise<ApiResult<T>>): Promise<T> {
  const started = performance.now();
  try {
    const result = await operation();
    if (result.data !== undefined) {
      recordPortalTelemetry({
        event: "api_request",
        route,
        durationMs: performance.now() - started,
        outcome: "success",
      });
      return result.data;
    }
    const problem = asProblem(result.error);
    const responseCorrelation = result.response?.headers.get("X-Correlation-ID") ?? null;
    throw new PortalApiError(problem?.detail ?? "The Portal API returned a safe error response.", {
      correlationId: problem?.correlation_id ?? responseCorrelation,
      retryable:
        problem?.retryable ?? (result.response?.status ? result.response.status >= 500 : true),
      status: result.response?.status ?? problem?.status ?? null,
      problem,
    });
  } catch (error) {
    recordPortalTelemetry({
      event: "api_request",
      route,
      durationMs: performance.now() - started,
      outcome: "failure",
    });
    if (error instanceof PortalApiError) {
      throw error;
    }
    throw new PortalApiError("The Portal API request failed or timed out.", {
      retryable: true,
      cause: error,
    });
  }
}

function signal(): AbortSignal {
  return AbortSignal.timeout(REQUEST_TIMEOUT_MS);
}

export const portalApi = {
  liveness(): Promise<LivenessResponse> {
    return unwrap("/health/live", () => sdk.getLiveness({ signal: signal() }));
  },
  readiness(): Promise<ReadinessResponse> {
    return unwrap("/health/ready", async () => {
      const result = await sdk.getReadiness({ signal: signal() });
      if (
        result.data === undefined &&
        result.error &&
        typeof result.error === "object" &&
        "status" in result.error &&
        (result.error as { status?: unknown }).status === "NOT_READY"
      ) {
        return { ...result, data: result.error as ReadinessResponse, error: undefined };
      }
      return result;
    });
  },
  systemInfo(): Promise<SystemInfoResponse> {
    return unwrap("/v1/system/info", () => sdk.getSystemInfo({ signal: signal() }));
  },
  dependencies(force = false): Promise<DependencyListResponse> {
    return unwrap("/v1/system/dependencies", () =>
      sdk.getSystemDependencies({ query: { force }, signal: signal() }),
    );
  },
};
