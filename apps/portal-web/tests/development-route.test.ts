import { describe, expect, it } from "vitest";

import { developmentRoutesEnabled } from "@/features/development/route-policy";

describe("development-only routes", () => {
  it("fail closed for production builds", () => {
    expect(developmentRoutesEnabled("production")).toBe(false);
  });

  it("are available for explicit development verification", () => {
    expect(developmentRoutesEnabled("development")).toBe(true);
  });
});
