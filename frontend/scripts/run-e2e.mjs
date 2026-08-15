import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(frontendRoot, "..");
const configDirectory = mkdtempSync(join(tmpdir(), "serversense-e2e-"));
const containerName = `serversense-e2e-${process.pid}-${Date.now()}`;
const imageName = "serversense:e2e";

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: repositoryRoot,
    encoding: "utf8",
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} failed with exit code ${result.status}`,
    );
  }
  return result.stdout?.trim() ?? "";
}

async function waitForHealth(baseUrl) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`);
      if (response.ok) return;
    } catch {
      // The container is still starting.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 500));
  }
  throw new Error(`ServerSense did not become healthy at ${baseUrl}`);
}

try {
  run("docker", ["build", "--build-arg", "VERSION=e2e", "-t", imageName, "."], {
    stdio: "inherit",
  });
  run("docker", [
    "run",
    "--rm",
    "-d",
    "--name",
    containerName,
    "-p",
    "127.0.0.1::8080",
    "-e",
    "SERVERSENSE_SECRET_KEY=e2e-only-secret-key-00000000000000000000000000000000",
    "-v",
    `${configDirectory}:/config`,
    imageName,
  ]);

  const publishedPort = run("docker", ["port", containerName, "8080/tcp"]);
  const portMatch = publishedPort.match(/:(\d+)$/m);
  if (!portMatch) throw new Error(`Unable to parse Docker port: ${publishedPort}`);
  const baseUrl = `http://127.0.0.1:${portMatch[1]}`;
  await waitForHealth(baseUrl);

  const playwrightCli = join(
    frontendRoot,
    "node_modules",
    "@playwright",
    "test",
    "cli.js",
  );
  run(process.execPath, [playwrightCli, "test"], {
    cwd: frontendRoot,
    env: { ...process.env, SERVERSENSE_E2E_BASE_URL: baseUrl },
    stdio: "inherit",
  });
} finally {
  spawnSync("docker", ["stop", containerName], { stdio: "ignore" });
  rmSync(configDirectory, { recursive: true, force: true });
}
