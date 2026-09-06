import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { clearLiveQueryCache, SLOW_REFRESH_INTERVAL_MS } from "../hooks/useLiveQuery";
import StoragePage from "./StoragePage";

vi.mock("../api", () => ({
  api: vi.fn(),
  formatBytes: (value: number) => `${value} B`,
}));

vi.mock("recharts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("recharts")>()),
  ResponsiveContainer: ({ children }: { children: ReactNode }) => children,
  ComposedChart: ({ data }: { data: unknown[] }) => (
    <div data-testid="storage-chart">{JSON.stringify(data)}</div>
  ),
}));

describe("storage live telemetry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api).mockReset();
    clearLiveQueryCache();
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

  it("keeps future forecasts out of the 24-hour history when switching ranges", async () => {
    const history = [{
      timestamp: "2026-09-05T12:00:00Z",
      total_bytes: 1_000,
      used_bytes: 600,
      free_bytes: 400,
      projected: false,
    }];
    vi.mocked(api).mockImplementation((path: string) => {
      if (path.startsWith("/api/storage/history")) return Promise.resolve(history);
      if (path === "/api/storage/pools") return Promise.resolve([]);
      return Promise.resolve({
        sampled_at: history[0].timestamp,
        current_total_bytes: 1_000,
        current_used_bytes: 600,
        current_free_bytes: 400,
        forecasts: [{
          window_days: 30,
          bytes_per_day: 2,
          days_remaining: 200,
          exhaustion_date: "2027-03-24T12:00:00Z",
          confidence: "high",
          sample_count: 720,
        }],
        recommended_window_days: 30,
      });
    });
    render(<StoragePage />);
    await act(async () => undefined);
    const chartData = () => JSON.parse(screen.getByTestId("storage-chart").textContent!);
    expect(chartData()).toHaveLength(13);

    fireEvent.click(screen.getByRole("button", { name: "24H" }));
    await act(async () => undefined);
    expect(api).toHaveBeenCalledWith("/api/storage/history?range=24h");
    expect(chartData()).toEqual(history);
    expect(screen.queryByText("Deterministic 30-day projection")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "7D" }));
    await act(async () => undefined);
    expect(chartData()).toHaveLength(13);
    expect(screen.getByText("Deterministic 30-day projection")).toBeVisible();
  });
});
