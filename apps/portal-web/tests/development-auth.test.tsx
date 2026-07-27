import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({
  notFound: vi.fn(() => {
    throw new Error("not-found");
  }),
  redirect: vi.fn(() => {
    throw new Error("redirect");
  }),
}));
const currentServerSession = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => navigation);
vi.mock("@/features/auth/server-session", () => ({
  currentServerSession,
}));

import DeveloperPage from "@/app/developer/page";

describe("development route authentication", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects an anonymous development request to governed login", async () => {
    currentServerSession.mockResolvedValue(null);

    await expect(DeveloperPage()).rejects.toThrow("redirect");

    expect(navigation.redirect).toHaveBeenCalledWith("/login?return_to=/developer");
  });

  it("renders development tools only for a current server session", async () => {
    currentServerSession.mockResolvedValue({ session_reference: "safe-reference" });

    render(await DeveloperPage());

    expect(screen.getByRole("heading", { name: "Portal developer information" })).toBeVisible();
    expect(navigation.redirect).not.toHaveBeenCalled();
  });
});
