import { Sdk } from "@fintech/portal-contracts";
import type {
  CapabilityListView,
  CsrfView,
  DependencyListResponse,
  EnvironmentListView,
  EnvironmentSelectionView,
  LivenessResponse,
  LogoutResult,
  NavigationView,
  ProblemDetails,
  ReadinessResponse,
  SessionView,
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

export async function withFreshCsrf<T>(
  fetchCsrf: () => Promise<CsrfView>,
  operation: (csrfToken: string) => Promise<T>,
): Promise<T> {
  const csrf = await fetchCsrf();
  return operation(csrf.csrf_token);
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
  dependencies(environmentId: string, force = false): Promise<DependencyListResponse> {
    return unwrap("/v1/system/dependencies", () =>
      sdk.getSystemDependencies({
        query: { environment_id: environmentId, force },
        signal: signal(),
      }),
    );
  },
  session(): Promise<SessionView> {
    return unwrap("/v1/session", () => sdk.getSession({ signal: signal() }));
  },
  csrf(): Promise<CsrfView> {
    return unwrap("/v1/session/csrf", () => sdk.getSessionCsrf({ signal: signal() }));
  },
  environments(): Promise<EnvironmentListView> {
    return unwrap("/v1/environments", () => sdk.listEnvironments({ signal: signal() }));
  },
  capabilities(environmentId: string): Promise<CapabilityListView> {
    return unwrap("/v1/capabilities", () =>
      sdk.listCapabilities({
        query: { environment_id: environmentId },
        signal: signal(),
      }),
    );
  },
  navigation(environmentId: string): Promise<NavigationView> {
    return unwrap("/v1/navigation", () =>
      sdk.getNavigation({
        query: { environment_id: environmentId },
        signal: signal(),
      }),
    );
  },
  async selectEnvironment(environmentId: string): Promise<EnvironmentSelectionView> {
    return withFreshCsrf(
      () => portalApi.csrf(),
      (csrfToken) =>
        unwrap("/v1/session/environment", () =>
          sdk.selectEnvironment({
            body: { environment_id: environmentId },
            headers: { "X-CSRF-Token": csrfToken },
            signal: signal(),
          }),
        ),
    );
  },
  async refreshSession(): Promise<SessionView> {
    return withFreshCsrf(
      () => portalApi.csrf(),
      (csrfToken) =>
        unwrap("/v1/session/refresh", () =>
          sdk.refreshSession({
            headers: { "X-CSRF-Token": csrfToken },
            signal: signal(),
          }),
        ),
    );
  },
  async logout(): Promise<LogoutResult> {
    return withFreshCsrf(
      () => portalApi.csrf(),
      (csrfToken) =>
        unwrap("/v1/auth/logout", () =>
          sdk.logout({
            headers: { "X-CSRF-Token": csrfToken },
            signal: signal(),
          }),
        ),
    );
  },
  async logoutAll(): Promise<LogoutResult> {
    return withFreshCsrf(
      () => portalApi.csrf(),
      (csrfToken) =>
        unwrap("/v1/auth/logout-all", () =>
          sdk.logoutAll({
            headers: { "X-CSRF-Token": csrfToken },
            signal: signal(),
          }),
        ),
    );
  },
};
