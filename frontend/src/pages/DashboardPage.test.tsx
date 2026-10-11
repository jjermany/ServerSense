import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { clearLiveQueryCache, LIVE_REFRESH_INTERVAL_MS } from "../hooks/useLiveQuery";
import type { Dashboard, DashboardSummaryStatus, StoragePoint } from "../types";
import DashboardPage from "./DashboardPage";

vi.mock("../api", () => ({
  api: vi.fn(),
  formatBytes: (value: number, digits = 1) =>
    `${(value / 1_000_000_000_000).toFixed(digits)} TB`,
  formatRate: (value: number | null) =>
    value == null ? "Learning" : `${value.toFixed(0)} B/s`,
}));

const dashboard: Dashboard = {
  updated_at: "2026-08-26T05:00:00Z",
  timezone: "America/Chicago",
  server: {
    name: "Tower",
    array_status: "started",
    uptime_seconds: 3600,
    pools: [],
  },
  storage: {
    sampled_at: "2026-08-26T05:00:00Z",
    total_bytes: 10_000_000_000_000,
    used_bytes: 6_000_000_000_000,
    free_bytes: 4_000_000_000_000,
    days_remaining: 240,
    growth_bytes_per_day: 20_000_000_000,
  },
  system: {
    sampled_at: "2026-08-26T05:00:00Z",
    cpu_percent: 12,
    memory_percent: 34,
    load_1m: 0.5,
    network_rx_bytes_per_second: 1500,
    network_tx_bytes_per_second: 500,
    network_sample_interval_seconds: 300,
  },
  disks: [
    {
      id: "disk-1",
      sampled_at: "2026-08-26T05:00:00Z",
      name: "Disk 1",
      role: "data",
      manufacturer: "Example",
      model: "Model",
      serial: "SERIAL",
      interface: "SATA",
      total_bytes: 10_000_000_000_000,
      used_bytes: 6_000_000_000_000,
      temperature_c: 38,
      smart_status: "healthy",
      smart_attributes: {},
    },
  ],
  containers: [
    {
      id: "container-1",
      name: "Plex",
      image: "plex:latest",
      status: "running",
      health: "healthy",
      uptime_seconds: 3600,
      last_state_change: null,
      cpu_percent: 2,
      memory_bytes: 512_000_000,
      restart_count: 0,
    },
  ],
  alerts: [],
  insights: [
    {
      severity: "info",
      title: "Storage trajectory",
      message: "Capacity is projected to last approximately 240 days.",
      source: "forecast",
      generated_at: "2026-08-26T05:00:00Z",
    },
  ],
  demo_mode: true,
};

const history: StoragePoint[] = [
  {
    timestamp: "2026-08-15T00:00:00Z",
    total_bytes: 10_000_000_000_000,
    used_bytes: 6_000_000_000_000,
    free_bytes: 4_000_000_000_000,
    projected: false,
  },
];

describe("dashboard", () => {
  beforeEach(() => {
    clearLiveQueryCache();
    vi.mocked(api).mockClear();
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(path === "/api/dashboard" ? dashboard : history),
    );
  });

  it("queues a manual summary refresh and keeps deterministic insights while waiting", async () => {
    let status: DashboardSummaryStatus = {
      status: "cooldown", can_refresh: true, message: "The last attempt failed. You can retry now.",
      retry_at: "2026-08-26T18:00:00Z", last_failure_category: "timeout",
      last_failure_at: "2026-08-26T17:00:00Z", generated_at: null,
      failure_guidance: "Dashboard facts exceeded a context limit. Check both Context window and Maximum AI context in SENSE AI.",
    };
    vi.mocked(api).mockImplementation((path: string) => {
      if (path.endsWith("/summary/refresh")) {
        status = { ...status, status: "queued", can_refresh: false, message: "Refresh requested.", retry_at: null };
        return Promise.resolve(status);
      }
      return Promise.resolve(path === "/api/dashboard" ? { ...dashboard, summary_status: status } : history);
    });
    render(<DashboardPage />);
    const refresh = await screen.findByRole("button", { name: "Refresh dashboard summary" });
    expect(screen.getByText(/Next automatic retry:.*1:00 PM/)).toBeVisible();
    expect(screen.getByText(/Dashboard facts exceeded a context limit/)).toBeVisible();
    fireEvent.click(refresh);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/dashboard/summary/refresh", { method: "POST" }));
    expect(await screen.findByText("Refresh requested.")).toBeVisible();
    expect(refresh).toBeDisabled();
    expect(screen.getByText("Storage trajectory")).toBeVisible();
  });

  it("explains opt-in and links directly to AI settings", async () => {
    vi.mocked(api).mockImplementation((path: string) => Promise.resolve(
      path === "/api/dashboard" ? { ...dashboard, summary_status: {
        status: "disabled", message: "Dashboard summaries are off.", can_refresh: false,
      } } : history,
    ));
    render(<MemoryRouter><DashboardPage /></MemoryRouter>);
    expect(await screen.findByText("Dashboard summaries are off.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Configure SENSE AI" })).toHaveAttribute("href", "/settings#ai");
    expect(screen.getByRole("button", { name: "Refresh dashboard summary" })).toBeDisabled();
  });

  it("allows retry when submitting a refresh fails", async () => {
    vi.mocked(api).mockImplementation((path: string) => {
      if (path.endsWith("/summary/refresh")) return Promise.reject(new Error("Refresh unavailable"));
      return Promise.resolve(path === "/api/dashboard" ? { ...dashboard, summary_status: {
        status: "pending", message: "No current summary is available.", can_refresh: true,
      } } : history);
    });
    render(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Refresh dashboard summary" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Refresh unavailable");
    expect(screen.getByRole("button", { name: "Refresh dashboard summary" })).toBeEnabled();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("refreshes overview metrics on the live interval", async () => {
    vi.useFakeTimers();
    let dashboardCalls = 0;
    vi.mocked(api).mockImplementation((path: string) => {
      if (path !== "/api/dashboard") return Promise.resolve(history);
      dashboardCalls += 1;
      return Promise.resolve({
        ...dashboard,
        system: {
          ...dashboard.system,
          cpu_percent: dashboardCalls === 1 ? 12 : 24,
        },
      });
    });

    render(<DashboardPage />);
    await act(async () => undefined);
    expect(screen.getByText("12.0% CPU")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
    });
    expect(screen.getByText("24.0% CPU")).toBeVisible();
  });

  it("renders measured demo telemetry without detectable accessibility violations", async () => {
    const { container } = render(<DashboardPage />);

    await waitFor(() =>
      expect(screen.getByText("Storage trajectory")).toBeInTheDocument(),
    );
    expect(screen.getByText(/realistic simulated Unraid telemetry/)).toBeVisible();
    expect(screen.getAllByText("4.00 TB")).toHaveLength(2);
    expect(screen.getByText(/Updated.*CDT/)).toBeVisible();

    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });

  it("formats a falling usage trend without a contradictory positive sign", async () => {
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(
        path === "/api/dashboard"
          ? {
              ...dashboard,
              storage: {
                ...dashboard.storage,
                growth_bytes_per_day: -846_702_280_547,
              },
            }
          : history,
      ),
    );

    render(<DashboardPage />);

    expect(await screen.findByText("-0.8 TB / day")).toBeVisible();
    expect(screen.queryByText(/\+-/)).not.toBeInTheDocument();
  });

  it("adds a cached AI summary without replacing measured insights", async () => {
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(
        path === "/api/dashboard"
          ? {
              ...dashboard,
              insights: [
                {
                  severity: "info",
                  title: "Current server summary",
                  message: "Media imports explain part of this week's measured growth.",
                  source: "sense",
                  kind: "dashboard_summary",
                  model: "small-local-model",
                  generated_at: "2026-08-26T04:00:00Z",
                },
                ...dashboard.insights,
              ],
            }
          : history,
      ),
    );

    render(<DashboardPage />);

    expect(await screen.findByText("Current server summary")).toBeVisible();
    expect(screen.getByText("Storage trajectory")).toBeVisible();
    expect(screen.getByText(/Cached SENSE summary/)).toHaveTextContent(
      "small-local-model",
    );
    expect(screen.getByText(/Cached SENSE summary/)).toHaveTextContent("Generated");
    expect(screen.getByText(/Based on measured telemetry/)).toBeVisible();
  });
});
