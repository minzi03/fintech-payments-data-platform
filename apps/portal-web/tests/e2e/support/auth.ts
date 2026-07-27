import { expect, type Page } from "@playwright/test";

export const localIdentity = {
  password: process.env.PORTAL_E2E_PASSWORD ?? "local-portal-test-password",
  username: process.env.PORTAL_E2E_USERNAME ?? "portfolio.user",
};

export async function loginWithLocalProvider(page: Page): Promise<string> {
  let callbackUrl = "";
  const tokenRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/portal-api/v1/auth/callback" && url.searchParams.has("code")) {
      callbackUrl = url.toString();
    }
    if (url.pathname.endsWith("/protocol/openid-connect/token")) {
      tokenRequests.push(url.toString());
    }
  });

  await page.goto("/");
  await page.getByRole("link", { name: "Sign in" }).click();
  await page
    .getByRole("button", { name: "Continue with the configured identity provider" })
    .click();
  await page.locator("#username").fill(localIdentity.username);
  await page.locator("#password").fill(localIdentity.password);
  await page.locator("#kc-login").click();

  await expect(page.getByText("Signed in")).toBeVisible();
  expect(callbackUrl).not.toBe("");
  expect(tokenRequests).toEqual([]);
  return callbackUrl;
}
