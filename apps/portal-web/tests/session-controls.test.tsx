import type { SessionView } from "@fintech/portal-contracts";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { portalApi } from "@/api/portal-api";
import { PortalApiError } from "@/api/problem";
import { Providers } from "@/app/providers";
import { SessionControls } from "@/features/auth/session-controls";
import { render } from "@testing-library/react";

vi.mock("@/api/portal-api", () => ({
  portalApi: {
    capabilities: vi.fn(),
    csrf: vi.fn(),
    environments: vi.fn(),
    logout: vi.fn(),
    logoutAll: vi.fn(),
    refreshSession: vi.fn(),
    selectEnvironment: vi.fn(),
    session: vi.fn(),
  },
}));

const mockedApi = vi.mocked(portalApi);
const session: SessionView = {
  absolute_expires_at: "2026-07-27T18:00:00Z",
  assurance: "AAL1",
  authenticated_at: "2026-07-27T10:00:00Z",
  capability_revision: "portal-capabilities-v1",
  environment_ids: ["local", "development"],
  idle_expires_at: "2026-07-27T11:00:00Z",
  policy_revision: "portal-policy-v1",
  principal_reference: "principal-safe-reference",
  roles: ["portal_viewer"],
  session_reference: "session-safe-reference",
  status: "ACTIVE",
  tenant_id: "fintech-platform-primary",
};

function renderControls() {
  return render(
    <Providers>
      <SessionControls />
    </Providers>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.capabilities.mockResolvedValue({
    capabilities: [],
    capability_revision: "portal-capabilities-v1",
  });
  mockedApi.environments.mockResolvedValue({
    capability_revision: "portal-capabilities-v1",
    environments: [
      { display_name: "Local", environment_id: "local" },
      { display_name: "Development", environment_id: "development" },
    ],
    policy_revision: "portal-policy-v1",
  });
  mockedApi.logout.mockResolvedValue({ revoked_session_count: 1 });
  mockedApi.logoutAll.mockResolvedValue({ revoked_session_count: 1 });
  mockedApi.refreshSession.mockResolvedValue(session);
  mockedApi.selectEnvironment.mockImplementation(async (environmentId) => ({
    capability_revision: "portal-capabilities-v1",
    environment_id: environmentId,
    policy_revision: "portal-policy-v1",
  }));
});

describe("session controls", () => {
  it("treats an explicit 401 as an anonymous session", async () => {
    mockedApi.session.mockRejectedValue(
      new PortalApiError("Authentication required.", { retryable: false, status: 401 }),
    );
    renderControls();
    expect(await screen.findByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("updates the environment hint only after the server accepts selection", async () => {
    const user = userEvent.setup();
    let acceptSelection!: (value: {
      capability_revision: string;
      environment_id: string;
      policy_revision: string;
    }) => void;
    mockedApi.session.mockResolvedValue(session);
    mockedApi.selectEnvironment.mockImplementation(
      () =>
        new Promise((resolve) => {
          acceptSelection = resolve;
        }),
    );
    renderControls();

    const selector = await screen.findByRole("combobox", { name: "Authorized environment" });
    await screen.findByRole("option", { name: "Local" });
    await user.selectOptions(selector, "local");
    expect(mockedApi.selectEnvironment).toHaveBeenCalledWith("local");
    expect(selector).toHaveValue("");

    acceptSelection({
      capability_revision: "portal-capabilities-v1",
      environment_id: "local",
      policy_revision: "portal-policy-v1",
    });
    await waitFor(() => expect(selector).toHaveValue("local"));
  });

  it("uses the server logout operation and returns to anonymous state", async () => {
    const user = userEvent.setup();
    mockedApi.session.mockResolvedValue(session);
    renderControls();

    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(mockedApi.logout).toHaveBeenCalledOnce());
    expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });
});
