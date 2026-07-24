import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["html", { open: "never" }], ["list"]] : "list",
  use: {
    baseURL: process.env.PORTAL_WEB_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: process.env.PORTAL_E2E_EXTERNAL
    ? undefined
    : [
        {
          command:
            "python -m uvicorn portal_api.main:app --app-dir ../portal-api/app --host 127.0.0.1 --port 8010",
          cwd: ".",
          reuseExistingServer: !process.env.CI,
          url: "http://127.0.0.1:8010/health/live",
        },
        {
          command: "pnpm dev",
          cwd: ".",
          env: {
            ...process.env,
            PORTAL_API_INTERNAL_URL: "http://127.0.0.1:8010",
          },
          reuseExistingServer: !process.env.CI,
          url: "http://127.0.0.1:3000",
        },
      ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
