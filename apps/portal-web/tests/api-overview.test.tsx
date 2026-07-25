import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { portalApi } from "@/api/portal-api";
import { PortalApiError } from "@/api/problem";
import { ApiOverview } from "@/features/system/api-overview";
import { renderWithProviders } from "@/tests/test-utils";

vi.mock("@/api/portal-api", () => ({
  portalApi: {
    dependencies: vi.fn(),
    liveness: vi.fn(),
    readiness: vi.fn(),
    systemInfo: vi.fn(),
  },
}));

const mockedApi = vi.mocked(portalApi);

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.dependencies.mockResolvedValue({
    correlation_id: "corr-dependencies",
    dependencies: [],
    observed_at: "2026-07-24T00:00:00Z",
  });
  mockedApi.liveness.mockResolvedValue({
    build_sha: "abc123",
    correlation_id: "corr-live",
    service: "portal-api",
    status: "UP",
    time: "2026-07-24T00:00:00Z",
    version: "0.1.0",
  });
  mockedApi.readiness.mockResolvedValue({
    api_contract_version: "1.0.0",
    correlation_id: "corr-ready",
    dependencies: [],
    observed_at: "2026-07-24T00:00:00Z",
    reason: "All enabled dependencies are ready.",
    service: "portal-api",
    status: "READY",
    version: "0.1.0",
  });
  mockedApi.systemInfo.mockResolvedValue({
    api_contract_version: "1.0.0",
    build_sha: "abc123",
    build_time: "2026-07-24T00:00:00Z",
    correlation_id: "corr-info",
    current_time: "2026-07-24T00:00:00Z",
    documentation_version: "portal-foundation-v1",
    runtime_environment: "test",
    service_name: "portal-api",
    service_version: "0.1.0",
    supported_api_versions: ["v1"],
  });
});

describe("API overview", () => {
  it("renders an accessible loading state", () => {
    mockedApi.liveness.mockReturnValue(new Promise(() => undefined));
    renderWithProviders(<ApiOverview />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking the BFF boundary");
  });

  it("renders the generated-contract ready response", async () => {
    renderWithProviders(<ApiOverview />);
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("corr-ready")).toBeInTheDocument();
    expect(screen.getByText("test")).toBeInTheDocument();
  });

  it("renders degraded state without claiming failure", async () => {
    mockedApi.readiness.mockResolvedValue({
      ...(await mockedApi.readiness()),
      reason: "An optional adapter is unavailable.",
      status: "DEGRADED",
    });
    renderWithProviders(<ApiOverview />);
    expect(await screen.findByText("Degraded")).toBeInTheDocument();
  });

  it("renders not-ready state", async () => {
    mockedApi.readiness.mockResolvedValue({
      ...(await mockedApi.readiness()),
      reason: "A required adapter is unavailable.",
      status: "NOT_READY",
    });
    renderWithProviders(<ApiOverview />);
    expect(await screen.findByText("Not ready")).toBeInTheDocument();
  });

  it("renders safe network errors and a correlation ID", async () => {
    mockedApi.liveness.mockRejectedValue(
      new PortalApiError("The request could not be completed.", {
        correlationId: "corr-failure",
        retryable: true,
        status: 503,
      }),
    );
    renderWithProviders(<ApiOverview />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText("corr-failure")).toBeInTheDocument();
    expect(screen.queryByText(/traceback|password|postgresql:\/\//i)).not.toBeInTheDocument();
  });
});
