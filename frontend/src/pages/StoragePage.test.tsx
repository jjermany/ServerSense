import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { SLOW_REFRESH_INTERVAL_MS } from "../hooks/useLiveQuery";
import StoragePage from "./StoragePage";

vi.mock("../api", () => ({
  api: vi.fn(),
  formatBytes: (value: number) => `${value} B`,
}));

describe("storage live telemetry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api).mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("refreshes forecast values at the slow UI interval", async () => {
    let forecastCalls = 0;
    vi.mocked(api).mockImplementation((path: string) => {
      if (path.startsWith("/api/storage/history")) {
        return Promise.resolve([
          {
            timestamp: "2026-08-26T05:00:00Z",
            total_bytes: 1_000,
            used_bytes: 900,
            free_bytes: 100,
            projected: false,
          },
        ]);
      }
      if (path === "/api/storage/pools") return Promise.resolve([]);
      forecastCalls += 1;
      const free = forecastCalls === 1 ? 100 : 80;
      return Promise.resolve({
        sampled_at: "2026-08-26T05:00:00Z",
        current_total_bytes: 1_000,
        current_used_bytes: 1_000 - free,
        current_free_bytes: free,
        forecasts: [],
        recommended_window_days: null,
      });
    });

    render(<StoragePage />);
    await act(async () => undefined);
    expect(screen.getByText("100 B")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SLOW_REFRESH_INTERVAL_MS);
    });
    expect(screen.getByText("80 B")).toBeVisible();
  });
});
