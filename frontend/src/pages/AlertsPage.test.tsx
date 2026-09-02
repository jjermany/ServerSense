import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { LIVE_REFRESH_INTERVAL_MS } from "../hooks/useLiveQuery";
import AlertsPage from "./AlertsPage";

vi.mock("../api", () => ({ api: vi.fn() }));

describe("AlertsPage", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("shows newly recorded alerts on the live refresh interval", async () => {
    vi.useFakeTimers();
    const newAlert = {
      id: 41,
      type: "container_stopped",
      severity: "warning",
      title: "Container stopped",
      message: "Plex has remained stopped.",
      created_at: "2026-08-18T12:00:00Z",
      active: true,
      acknowledged_at: null,
    };
    vi.mocked(api).mockResolvedValueOnce([]).mockResolvedValueOnce([newAlert]);

    render(
      <MemoryRouter>
        <AlertsPage />
      </MemoryRouter>,
    );
    await act(async () => undefined);
    expect(screen.getByText("No alerts have been recorded.")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_REFRESH_INTERVAL_MS);
    });
    expect(screen.getByText("Container stopped")).toBeVisible();
  });

  it("replaces the acknowledge button with the persisted acknowledgement", async () => {
    const activeAlert = {
      id: 42,
      type: "disk_temperature",
      severity: "warning",
      title: "Disk is hot",
      message: "Disk temperature is 52°C.",
      created_at: "2026-08-18T12:00:00Z",
      active: true,
      acknowledged_at: null,
    };
    vi.mocked(api)
      .mockResolvedValueOnce([activeAlert])
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce([
        { ...activeAlert, acknowledged_at: "2026-08-18T12:05:00Z" },
      ]);

    render(
      <MemoryRouter>
        <AlertsPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /acknowledge/i }));

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /acknowledge/i })).toBeNull();
      expect(screen.getByText(/^Acknowledged /)).toBeInTheDocument();
    });
    expect(api).toHaveBeenNthCalledWith(2, "/api/alerts/42/acknowledge", {
      method: "POST",
    });
  });

  it("dismisses an alert and removes it from the page", async () => {
    const alert = {
      id: 43,
      type: "storage_low",
      severity: "warning",
      title: "Storage is low",
      message: "Only 8% remains.",
      created_at: "2026-08-18T12:00:00Z",
      active: true,
      acknowledged_at: null,
    };
    vi.mocked(api).mockResolvedValueOnce([alert]).mockResolvedValueOnce({ ok: true });

    render(
      <MemoryRouter>
        <AlertsPage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /dismiss/i }));

    await waitFor(() => {
      expect(screen.queryByText("Storage is low")).toBeNull();
      expect(screen.getByText("No alerts have been recorded.")).toBeInTheDocument();
    });
    expect(api).toHaveBeenNthCalledWith(2, "/api/alerts/43/dismiss", {
      method: "POST",
    });
  });
});
