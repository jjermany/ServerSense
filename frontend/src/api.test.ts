import { afterEach, describe, expect, it, vi } from "vitest";
import { API_REQUEST_TIMEOUT_MS, api, formatBytes } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("formatBytes", () => {
  it("formats decimal storage units consistently", () => {
    expect(formatBytes(4_070_000_000_000, 2)).toBe("4.07 TB");
    expect(formatBytes(620_000_000_000)).toBe("620.0 GB");
    expect(formatBytes(-846_702_280_547)).toBe("-846.7 GB");
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

  it("exposes response status for authentication decisions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: vi.fn().mockResolvedValue({ detail: "Authentication required" }),
      }),
    );

    await expect(api("/api/auth/me")).rejects.toEqual(
      expect.objectContaining({
        message: "Authentication required",
        name: "ApiError",
        status: 401,
      }),
    );
  });

  it("aborts a request that does not complete", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn((_path: string, options: RequestInit) =>
      new Promise((_resolve, reject) => {
        options.signal?.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
      }),
    );
    vi.stubGlobal("fetch", fetch);

    const request = expect(api("/api/dashboard")).rejects.toThrow(
      "Server did not respond in time",
    );
    await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS);
    await request;
    vi.useRealTimers();
  });
});
