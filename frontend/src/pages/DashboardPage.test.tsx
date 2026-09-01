import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Dashboard, StoragePoint } from "../types";
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
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(path === "/api/dashboard" ? dashboard : history),
    );
  });
  afterEach(cleanup);

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
