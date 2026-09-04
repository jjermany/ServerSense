import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import App, { AUTH_RETRY_DELAY_MS } from "./App";

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return { ...original, api: vi.fn() };
});

describe("application authentication bootstrap", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("recovers automatically after a temporary server failure", async () => {
    vi.useFakeTimers();
    vi.mocked(api)
      .mockRejectedValueOnce(new Error("database is locked"))
      .mockResolvedValueOnce({ setup_required: true });

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );

    await act(async () => undefined);
    expect(
      screen.getByText("Server is taking longer than expected. Retrying…"),
    ).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTH_RETRY_DELAY_MS);
    });
    expect(
      screen.getByText("Welcome to ServerSense"),
    ).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(2);
  });
});
