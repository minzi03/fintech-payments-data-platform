import { describe, expect, it, vi } from "vitest";

import { withFreshCsrf } from "@/api/portal-api";

describe("Portal API authenticated mutations", () => {
  it("fetches a short-lived CSRF value immediately before environment selection", async () => {
    const order: string[] = [];
    const fetchCsrf = vi.fn(async () => {
      order.push("csrf");
      return { csrf_token: "csrf-ephemeral", generation: 2 };
    });
    const mutation = vi.fn(async (csrfToken: string) => {
      order.push("mutation");
      return { accepted: true, csrfToken };
    });

    const result = await withFreshCsrf(fetchCsrf, mutation);

    expect(order).toEqual(["csrf", "mutation"]);
    expect(mutation).toHaveBeenCalledWith("csrf-ephemeral");
    expect(result).toEqual({ accepted: true, csrfToken: "csrf-ephemeral" });
  });
});
