import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.SERVERSENSE_E2E_BASE_URL,
    screenshot: "only-on-failure",
    trace: "on-first-retry",
    ...devices["Desktop Chrome"],
  },
});
