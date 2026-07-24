import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import { AppShell } from "@/components/app-shell";

describe("foundation shell", () => {
  it("renders landmarks, environment, and no fabricated metrics", () => {
    render(
      <AppShell>
        <h1>Foundation content</h1>
      </AppShell>,
    );

    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Foundation navigation" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByLabelText("Environment: local")).toBeInTheDocument();
    expect(
      screen.queryByText(/revenue|transactions per second|active customers/i),
    ).not.toBeInTheDocument();
  });

  it("exposes a keyboard-reachable skip link", async () => {
    const user = userEvent.setup();
    render(
      <AppShell>
        <h1>Foundation content</h1>
      </AppShell>,
    );

    await user.tab();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveFocus();
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(
      <AppShell>
        <h1>Foundation content</h1>
      </AppShell>,
    );
    const results = await axe(container);
    expect(results.violations).toEqual([]);
  });
});
