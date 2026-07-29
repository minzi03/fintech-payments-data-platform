import { expect, test } from "@playwright/test";

import { loginWithLocalProvider } from "../support/auth";

test.skip(!process.env.PORTAL_E2E_AUTH, "requires the disposable local OIDC stack");

test("real OIDC login creates only an opaque HttpOnly Portal session", async ({
  context,
  page,
}) => {
  await loginWithLocalProvider(page);
  await page.getByRole("combobox", { name: "Authorized environment" }).selectOption("local");
  await expect(page.getByRole("combobox", { name: "Authorized environment" })).toHaveValue("local");

  await page.getByRole("link", { name: "System status", exact: true }).click();
  await expect(page.getByText("Portal PostgreSQL", { exact: true })).toBeVisible();
  await expect(page.getByText("Portal OIDC provider", { exact: true })).toBeVisible();

  const portalCookies = await context.cookies("http://localhost:3000");
  const sessionCookie = portalCookies.find((cookie) => cookie.name.includes("portal_session"));
  expect(sessionCookie).toMatchObject({
    httpOnly: true,
    sameSite: "Lax",
  });
  expect(
    portalCookies.some((cookie) => /access.?token|id.?token|refresh.?token/i.test(cookie.name)),
  ).toBe(false);
  expect(
    await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    })),
  ).toEqual({ local: [], session: [] });

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
});
