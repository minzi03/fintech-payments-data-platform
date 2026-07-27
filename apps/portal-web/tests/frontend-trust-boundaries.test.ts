import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const root = process.cwd();
const governedSources = [
  "api/portal-api.ts",
  "app/auth/start/route.ts",
  "app/login/page.tsx",
  "features/auth/authorized-navigation.tsx",
  "features/auth/session-context.tsx",
  "features/auth/session-controls.tsx",
].map((path) => readFileSync(join(root, path), "utf8"));

describe("frontend trust boundaries", () => {
  it("does not persist authentication material in browser storage", () => {
    const source = governedSources.join("\n");
    expect(source).not.toMatch(/\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b/);
  });

  it("does not invoke the OIDC callback contract from browser application code", () => {
    const browserSources = governedSources
      .filter((source) => !source.includes('from "node:crypto"'))
      .join("\n");
    expect(browserSources).not.toContain("completeLoginCallback");
    expect(browserSources).not.toMatch(/\baccess_token\b|\bid_token\b|\brefresh_token\b/);
  });
});
