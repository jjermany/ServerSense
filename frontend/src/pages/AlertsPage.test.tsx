import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import AlertsPage from "./AlertsPage";

vi.mock("../api", () => ({ api: vi.fn() }));

describe("AlertsPage", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
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
});
