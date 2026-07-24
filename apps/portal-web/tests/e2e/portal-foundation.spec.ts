import { expect, test } from "@playwright/test";

test("foundation connects through the BFF boundary", async ({ page }) => {
  const browserDestinations: string[] = [];
  page.on("request", (request) => browserDestinations.push(request.url()));

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("secure foundation");
  await expect(
    page.getByText("Operational data-platform capabilities are not enabled yet."),
  ).toBeVisible();
  await expect(page.getByText("Ready").first()).toBeVisible();

  await page.getByRole("link", { name: "System Status", exact: true }).click();
  await expect(page.getByRole("heading", { name: "System Status" })).toBeVisible();
  await expect(page.getByText("Liveness", { exact: true })).toBeVisible();
  await expect(page.getByText("Readiness", { exact: true })).toBeVisible();
  await expect(page.getByText("No infrastructure adapters are enabled.")).toBeVisible();

  const problem = await page.evaluate(async () => {
    const response = await fetch("/portal-api/not-a-real-route", {
      headers: { "X-Correlation-ID": "portal-e2e-correlation" },
    });
    return {
      body: await response.json(),
      header: response.headers.get("X-Correlation-ID"),
      status: response.status,
    };
  });
  expect(problem.status).toBe(404);
  expect(problem.header).toBe("portal-e2e-correlation");
  expect(problem.body.correlation_id).toBe("portal-e2e-correlation");
  expect(problem.body.error_code).toBe("RESOURCE_NOT_FOUND");

  const forbiddenPorts = [":5432", ":8080", ":8083", ":9000", ":9092", ":29092"];
  expect(
    browserDestinations.filter((url) => forbiddenPorts.some((port) => url.includes(port))),
  ).toEqual([]);
});
