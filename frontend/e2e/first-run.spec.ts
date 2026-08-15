import { expect, test } from "@playwright/test";

test("fresh installation completes every setup stage and serves the application", async ({
  page,
  request,
}) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Welcome to ServerSense" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Begin setup" }).click();
  await page.getByLabel("Server name", { exact: true }).fill("E2E Tower");
  await page.getByLabel("Username", { exact: true }).fill("e2eadmin");
  await page.getByLabel(/^Password/).fill("e2e-verification-password");
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(
    page.getByRole("heading", { name: "Choose monitoring mode" }),
  ).toBeVisible();
  await expect(page.getByText("FIRST LAUNCH · 3 OF 3")).toBeVisible();
  const preFinishStatus = await request.get("/api/auth/status");
  expect(await preFinishStatus.json()).toEqual({ setup_required: true });

  await page.getByLabel(/Start with demo data/).check();
  await page.getByRole("button", { name: "Finish setup" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await expect(page.getByText(/realistic simulated Unraid telemetry/)).toBeVisible();
  await expect(page.getByRole("heading", { name: /Good (morning|afternoon|evening)\./ })).toBeVisible();

  const routes = [
    ["Storage", "/storage", "Array capacity"],
    ["Disks", "/disks", "Physical disks"],
    ["Docker", "/docker", "Docker"],
    ["Alerts", "/alerts", "Alerts"],
    ["Ask SENSE", "/sense", "Ask SENSE"],
    ["Settings", "/settings", "Settings"],
  ] as const;
  for (const [link, path, heading] of routes) {
    await page.getByRole("link", { name: link, exact: true }).click();
    await expect(page).toHaveURL(path);
    await expect(page.getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
  }
  await expect(page.getByLabel(/Explain new alerts with SENSE/)).not.toBeChecked();
  await page.getByRole("link", { name: "Storage", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Storage pools" })).toBeVisible();
  await expect(page.getByText("cache", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Ask SENSE", exact: true }).click();
  await page
    .getByRole("button", { name: "How long until I run out of storage?" })
    .click();
  await expect(page.getByText("Checked storage forecast")).toBeVisible();
  await expect(page.getByText(/ServerSense currently measures/)).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Storage", exact: true }).click();
  await expect(page).toHaveURL("/storage");
  await expect(page.getByRole("heading", { name: "Array capacity" })).toBeVisible();

  expect(browserErrors).toEqual([]);
});
