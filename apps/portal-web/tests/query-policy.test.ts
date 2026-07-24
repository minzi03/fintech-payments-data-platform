import { describe, expect, it } from "vitest";

import { PortalApiError } from "@/api/problem";
import { shouldRetry } from "@/app/providers";

describe("query retry policy", () => {
  it("never retries authorization or validation failures", () => {
    expect(shouldRetry(0, new PortalApiError("forbidden", { status: 403 }))).toBe(false);
    expect(shouldRetry(0, new PortalApiError("invalid", { status: 422 }))).toBe(false);
  });

  it("bounds retryable read failures", () => {
    const error = new PortalApiError("temporary", { retryable: true, status: 503 });
    expect(shouldRetry(0, error)).toBe(true);
    expect(shouldRetry(2, error)).toBe(false);
  });
});
