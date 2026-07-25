import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { portalApi } from "@/api/portal-api";
import { SystemStatusPanel } from "@/features/system/system-status-panel";
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
  mockedApi.liveness.mockResolvedValue({
    build_sha: "local",
    correlation_id: "corr-live",
    service: "portal-api",
    status: "UP",
    time: "2026-07-24T00:00:00Z",
    version: "0.1.0-dev",
  });
  mockedApi.readiness.mockResolvedValue({
    api_contract_version: "1.0.0",
    correlation_id: "corr-ready",
    dependencies: [],
    observed_at: "2026-07-24T00:00:00Z",
    reason: "No required adapters are configured.",
    service: "portal-api",
    status: "READY",
    version: "0.1.0-dev",
  });
  mockedApi.systemInfo.mockResolvedValue({
    api_contract_version: "1.0.0",
    build_sha: "local",
    build_time: "local",
    correlation_id: "corr-info",
    current_time: "2026-07-24T00:00:00Z",
    documentation_version: "portal-foundation-v1",
    runtime_environment: "test",
    service_name: "portal-api",
    service_version: "0.1.0-dev",
    supported_api_versions: ["v1"],
  });
});

describe("System Status", () => {
  it("truthfully renders an empty adapter registry", async () => {
    mockedApi.dependencies.mockResolvedValue({
      correlation_id: "corr-dependencies",
      dependencies: [],
      observed_at: "2026-07-24T00:00:00Z",
    });
    renderWithProviders(<SystemStatusPanel />);
    expect(await screen.findByText("No infrastructure adapters are enabled.")).toBeInTheDocument();
    expect(screen.getByText("0 configured")).toBeInTheDocument();
  });

  it("renders a configured dependency and its safe reason", async () => {
    mockedApi.dependencies.mockResolvedValue({
      correlation_id: "corr-dependencies",
      observed_at: "2026-07-24T00:00:00Z",
      dependencies: [
        {
          adapter_version: "1.0.0",
          dependency_id: "airflow-health",
          dependency_type: "AIRFLOW",
          display_name: "Airflow API",
          observed_at: "2026-07-24T00:00:00Z",
          reason: "The optional endpoint timed out.",
          required: false,
          status: "TIMEOUT",
        },
      ],
    });
    renderWithProviders(<SystemStatusPanel />);
    expect(await screen.findByText("Airflow API")).toBeInTheDocument();
    expect(screen.getByText("Timed out")).toBeInTheDocument();
    expect(screen.getByText("The optional endpoint timed out.")).toBeInTheDocument();
  });
});
