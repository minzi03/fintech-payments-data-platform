import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { loginWithLocalProvider } from "../support/auth";

test.skip(!process.env.PORTAL_E2E_AUTH, "requires the disposable local OIDC stack");
test.skip(
  !process.env.PORTAL_E2E_PROVIDER_LIFECYCLE,
  "requires the disposable local PostgreSQL control plane",
);

const repositoryRoot = path.resolve(__dirname, "../../../../../");

function databaseScalar(statement: string): string {
  return execFileSync(
    "docker",
    [
      "compose",
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
    ],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
      windowsHide: true,
    },
  ).trim();
}

test("real Keycloak refresh rotates server-side tokens and logout disposes them", async ({
  context,
  page,
}) => {
  await loginWithLocalProvider(page);

  expect(
    databaseScalar(
      "UPDATE portal_control.portal_token_envelopes " +
        "SET expires_at = CURRENT_TIMESTAMP + INTERVAL '10 seconds' " +
        "WHERE envelope_id = (" +
        "SELECT envelope_id FROM portal_control.portal_token_envelopes " +
        "WHERE lifecycle_state = 'ACTIVE' ORDER BY created_at DESC LIMIT 1" +
        ") RETURNING token_generation",
    ),
  ).toBe("1");

  await expect
    .poll(
      () =>
        databaseScalar(
          "SELECT token_generation::text FROM portal_control.portal_token_envelopes " +
            "WHERE lifecycle_state = 'ACTIVE' ORDER BY created_at DESC LIMIT 1",
        ),
      { timeout: 20_000 },
    )
    .toBe("2");

  await expect(page.getByText("Signed in")).toBeVisible();
  const portalCookies = await context.cookies("http://localhost:3000");
  expect(
    portalCookies.some((cookie) => /access.?token|id.?token|refresh.?token/i.test(cookie.name)),
  ).toBe(false);

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();

  await expect
    .poll(() =>
      databaseScalar(
        "SELECT lifecycle_state FROM portal_control.portal_token_envelopes " +
          "ORDER BY created_at DESC LIMIT 1",
      ),
    )
    .toBe("DISPOSED");
  expect(
    Number(
      databaseScalar(
        "SELECT count(*) FROM portal_control.security_audit_events " +
          "WHERE event_type IN (" +
          "'auth.provider_refresh_succeeded.v1'," +
          "'auth.provider_logout_succeeded.v1'," +
          "'auth.provider_revocation_succeeded.v1'," +
          "'auth.provider_token_disposed.v1')",
      ),
    ),
  ).toBeGreaterThanOrEqual(5);
});
