import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { SLOW_REFRESH_INTERVAL_MS } from "../hooks/useLiveQuery";
import type { Disk } from "../types";
import DiskDetailsPage from "./DiskDetailsPage";
import DisksPage from "./DisksPage";

vi.mock("../api", () => ({
  api: vi.fn(),
  formatBytes: (value: number) => `${value} B`,
}));

const disk = (temperature: number): Disk => ({
  id: "disk-1",
  sampled_at: "2026-08-26T05:00:00Z",
  name: "Disk 1",
  role: "data",
  manufacturer: "Example",
  model: "Model",
  serial: "SERIAL",
  interface: "SATA",
  total_bytes: 1_000,
  used_bytes: 500,
  temperature_c: temperature,
  smart_status: "healthy",
  smart_attributes: {},
});

describe("disk live telemetry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api).mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("refreshes the disk inventory at the slow UI interval", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce([disk(38)])
      .mockResolvedValueOnce([disk(42)]);
    render(
      <MemoryRouter>
        <DisksPage />
      </MemoryRouter>,
    );
    await act(async () => undefined);
    expect(screen.getByText("38°C")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SLOW_REFRESH_INTERVAL_MS);
    });
    expect(screen.getByText("42°C")).toBeVisible();
  });

  it("refreshes disk details at the slow UI interval", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ ...disk(38), temperature_history: [] })
      .mockResolvedValueOnce({ ...disk(42), temperature_history: [] });
    render(
      <MemoryRouter initialEntries={["/disks/disk-1"]}>
        <Routes>
          <Route path="/disks/:diskId" element={<DiskDetailsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    await act(async () => undefined);
    expect(screen.getByText("38°C")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SLOW_REFRESH_INTERVAL_MS);
    });
    expect(screen.getByText("42°C")).toBeVisible();
  });
});
