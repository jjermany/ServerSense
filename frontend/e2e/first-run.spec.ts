import { expect, test } from "@playwright/test";
import { createHmac } from "node:crypto";
import { readFile } from "node:fs/promises";

function authenticatorCode(secret: string, offset = 0): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const bits = [...secret]
    .map((character) =>
      alphabet.indexOf(character).toString(2).padStart(5, "0"),
    )
    .join("");
  const key = Buffer.from(
    bits.match(/.{8}/g)!.map((byte) => parseInt(byte, 2)),
  );
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000) + offset));
  const digest = createHmac("sha1", key).update(counter).digest();
  const start = digest[digest.length - 1] & 15;
  return String((digest.readUInt32BE(start) & 0x7fffffff) % 1_000_000).padStart(
    6,
    "0",
  );
}

test("fresh installation completes every setup stage and serves the application", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000);
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
  await expect(
    page.getByText(/realistic simulated Unraid telemetry/),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Good (morning|afternoon|evening)\./ }),
  ).toBeVisible();

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
    await expect(
      page.getByRole("heading", { name: heading, exact: true }).first(),
    ).toBeVisible();
  }
  await expect(
    page.getByLabel(/Explain new alerts with SENSE/),
  ).not.toBeChecked();
  await page.getByRole("link", { name: "Storage", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Storage pools" }),
  ).toBeVisible();
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
  await expect(
    page.getByRole("heading", { name: "Array capacity" }),
  ).toBeVisible();

  await page.setViewportSize({ width: 1365, height: 1000 });
  await page.goto("/settings#security");
  await expect(page.getByText("Authenticator MFA:")).toContainText("Off");
  await page
    .getByLabel("Current password", { exact: true })
    .fill("e2e-verification-password");
  await page.getByRole("button", { name: "Set up MFA", exact: true }).click();
  const qr = page.getByAltText("Scan this QR code with your authenticator app");
  await expect(qr).toBeVisible();
  await expect
    .poll(() =>
      qr.evaluate((image) => (image as HTMLImageElement).naturalWidth),
    )
    .toBeGreaterThan(0);
  const secret = await page.getByLabel("Manual setup key").inputValue();
  const securityCard = page
    .locator(".settings-card")
    .filter({ has: page.getByRole("heading", { name: "Account security" }) });
  await securityCard.screenshot({
    path: testInfo.outputPath("mfa-setup-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: "Security", exact: true }).click();
  await expect
    .poll(async () => {
      const heading = await page
        .getByRole("heading", { name: "Account security" })
        .boundingBox();
      const navigation = await page
        .getByRole("complementary", { name: "Settings sections" })
        .boundingBox();
      return Boolean(
        heading && navigation && heading.y >= navigation.y + navigation.height,
      );
    })
    .toBe(true);
  await page.screenshot({ path: testInfo.outputPath("mfa-setup-mobile.png") });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page
    .getByLabel("Authenticator code", { exact: true })
    .fill(authenticatorCode(secret));
  await page.getByRole("button", { name: "Verify and enable MFA" }).click();
  const recoverySection = page.getByRole("region", { name: "Recovery codes" });
  await expect(recoverySection).toBeVisible();
  const recoveryCodes = await recoverySection.locator("code").allTextContents();
  expect(recoveryCodes).toHaveLength(10);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download recovery codes" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("serversense-recovery-codes.txt");
  expect(await readFile((await download.path())!, "utf8")).toContain(
    recoveryCodes[0],
  );
  await page.getByRole("button", { name: "I saved my recovery codes" }).click();
  await page.setViewportSize({ width: 1365, height: 1000 });

  const passwordLogin = async () => {
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.getByLabel("Username", { exact: true }).fill("E2EADMIN");
    await page
      .getByLabel("Password", { exact: true })
      .fill("e2e-verification-password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
  };
  await passwordLogin();
  await expect(
    page.getByRole("heading", { name: "Verify your sign-in" }),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("mfa-login.png") });
  await page
    .getByLabel("Authenticator or recovery code")
    .fill(authenticatorCode(secret, 1));
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await passwordLogin();
  await page
    .getByLabel("Authenticator or recovery code")
    .fill(recoveryCodes[0]);
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await page.goto("/settings#security");
  await expect(
    page.getByText(/You have 9 recovery codes remaining/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Replace recovery codes" }).click();
  await page
    .getByLabel("Current password", { exact: true })
    .fill("e2e-verification-password");
  await page
    .getByLabel("Authenticator or recovery code")
    .fill(recoveryCodes[1]);
  await page
    .getByRole("button", { name: "Generate new recovery codes" })
    .click();
  await expect(recoverySection).toBeVisible();
  const replacementCodes = await recoverySection
    .locator("code")
    .allTextContents();
  await page.getByRole("button", { name: "I saved my recovery codes" }).click();
  await page.getByRole("button", { name: "Disable MFA", exact: true }).click();
  await page
    .getByLabel("Current password", { exact: true })
    .fill("e2e-verification-password");
  await page
    .getByLabel("Authenticator or recovery code")
    .fill(replacementCodes[0]);
  await page.getByRole("button", { name: "Confirm disable MFA" }).click();
  await expect(page.getByText("Authenticator MFA:")).toContainText("Off");
  await passwordLogin();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();

  expect(browserErrors).toEqual([]);
});
