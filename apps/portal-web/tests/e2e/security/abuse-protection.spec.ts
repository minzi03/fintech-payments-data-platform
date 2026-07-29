import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { loginWithLocalProvider } from "../support/auth";

test.skip(!process.env.PORTAL_E2E_AUTH, "requires the disposable local OIDC stack");
test.skip(!process.env.PORTAL_E2E_ABUSE, "requires the disposable Redis abuse backend");

const repositoryRoot = path.resolve(__dirname, "../../../../../");

function compose(...arguments_: string[]): string {
  return execFileSync("docker", ["compose", ...arguments_], {
    cwd: repositoryRoot,
    encoding: "utf8",
    windowsHide: true,
  }).trim();
}

function databaseScalar(statement: string): string {
  return compose(
    "exec",
    "-T",
    "portal-postgres",
    "psql",
    "-U",
    "portal_admin",
    "-d",
    "portal_control",
    "-v",
    "ON_ERROR_STOP=1",
    "-q",
    "-tAc",
    statement,
  );
}

test("distributed login burst returns a generic bounded 429", async ({ request }) => {
  compose("exec", "-T", "portal-redis", "redis-cli", "FLUSHDB");
  let limited:
    | {
        status: number;
        headers: Record<string, string>;
        body: Record<string, unknown>;
      }
    | undefined;

  for (let index = 0; index < 15; index += 1) {
    const response = await request.post("http://localhost:8010/v1/auth/login", {
      data: { intent_token: `invalid-intent-${index}` },
      headers: { Origin: "http://localhost:3000" },
      maxRedirects: 0,
    });
    if (response.status() === 429) {
      limited = {
        status: response.status(),
        headers: response.headers(),
        body: (await response.json()) as Record<string, unknown>,
      };
      break;
    }
  }

  expect(limited?.status).toBe(429);
  expect(limited?.headers["retry-after"]).toMatch(/^\d+$/);
  expect(limited?.headers["cache-control"]).toBe("no-store");
  expect(limited?.body.error_code).toBe("RATE_LIMITED");
  expect(String(limited?.body.detail)).not.toMatch(/redis|bucket|identity|session/i);
});

test("local logout and crypto-erasure complete while Redis is unavailable", async ({
  context,
  page,
}) => {
  compose("exec", "-T", "portal-redis", "redis-cli", "FLUSHDB");
  await loginWithLocalProvider(page);
  compose("stop", "portal-redis");
  try {
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
    const cookies = await context.cookies("http://localhost:3000");
    expect(cookies.some((cookie) => cookie.name === "fintech_portal_session_v1")).toBe(false);
    await expect
      .poll(() =>
        databaseScalar(
          "SELECT lifecycle_state FROM portal_control.portal_token_envelopes " +
            "ORDER BY created_at DESC LIMIT 1",
        ),
      )
      .toBe("DISPOSED");
  } finally {
    compose("start", "portal-redis");
  }
});
