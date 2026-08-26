import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Container } from "../types";
import { LIVE_REFRESH_INTERVAL_MS } from "../hooks/useLiveQuery";
import DockerPage from "./DockerPage";

vi.mock("../api", () => ({
  api: vi.fn(),
  formatBytes: (value: number) => `${value} B`,
}));

const container = (status: string): Container => ({
  id: "container-1",
  name: "Plex",
  image: "plex:latest",
  status,
  health: null,
  uptime_seconds: 3600,
  last_state_change: null,
  cpu_percent: 2,
  memory_bytes: 512,
  restart_count: 0,
});

describe("Docker live telemetry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api).mockResolvedValue([container("running")]);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("refreshes the visible table on the live polling interval", async () => {
    render(<DockerPage />);
    await act(async () => undefined);
    expect(screen.getByText("1 of 1 online")).toBeVisible();

    vi.mocked(api).mockResolvedValue([container("exited")]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
    });

    expect(screen.getByText("0 of 1 online")).toBeVisible();
    expect(api).toHaveBeenCalledTimes(2);
  });
});
