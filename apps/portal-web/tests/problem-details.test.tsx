import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PortalApiError } from "@/api/problem";
import { ProblemAlert } from "@/components/problem-alert";

describe("Problem Details rendering", () => {
  it("shows only the safe detail and correlation identifier", () => {
    render(
      <ProblemAlert
        error={
          new PortalApiError("The service is not ready.", {
            correlationId: "corr-safe",
            status: 503,
          })
        }
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("The service is not ready.");
    expect(screen.getByRole("alert")).toHaveTextContent("corr-safe");
    expect(screen.queryByText(/stack|secret|cookie/i)).not.toBeInTheDocument();
  });
});
