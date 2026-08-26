import { afterEach, describe, expect, it, vi } from "vitest";
import { api, formatBytes } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("formatBytes", () => {
  it("formats decimal storage units consistently", () => {
    expect(formatBytes(4_070_000_000_000, 2)).toBe("4.07 TB");
    expect(formatBytes(620_000_000_000)).toBe("620.0 GB");
  });
});

describe("api", () => {
  it("bypasses browser caches for live API reads", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetch);

    await api("/api/dashboard");

    expect(fetch).toHaveBeenCalledWith(
      "/api/dashboard",
      expect.objectContaining({ cache: "no-store" }),
    );
  });
});
