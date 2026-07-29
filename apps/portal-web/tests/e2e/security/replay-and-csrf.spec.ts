import { expect, test } from "@playwright/test";

import { loginWithLocalProvider } from "../support/auth";

test.skip(!process.env.PORTAL_E2E_AUTH, "requires the disposable local OIDC stack");

test("callback replay and a mutation without CSRF both fail closed", async ({ page }) => {
  const callbackUrl = await loginWithLocalProvider(page);

  const csrfFailure = await page.evaluate(async () => {
    const response = await fetch("/portal-api/v1/session/environment", {
      body: JSON.stringify({ environment_id: "local" }),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    });
    return {
      body: await response.json(),
      status: response.status,
    };
  });
  expect(csrfFailure.status).toBe(403);
  expect(csrfFailure.body.detail).toBe("The request could not be accepted.");

  const replay = await page.request.get(callbackUrl, { maxRedirects: 0 });
  expect(replay.status()).toBe(401);
  expect((await replay.json()).detail).toBe("The authentication callback could not be accepted.");

  const session = await page.request.get("/portal-api/v1/session");
  expect(session.status()).toBe(200);
  expect((await session.json()).status).toBe("ACTIVE");
});
