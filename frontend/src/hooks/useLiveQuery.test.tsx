import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { useLiveQuery } from "./useLiveQuery";

vi.mock("../api", () => ({ api: vi.fn() }));

function Probe({ intervalMs }: { intervalMs: number }) {
  const { data } = useLiveQuery<{ value: number }>("/api/live", intervalMs);
  return <span>{data?.value ?? "loading"}</span>;
}

describe("useLiveQuery", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api).mockReset();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("uses the configured interval and refreshes when a hidden tab becomes visible", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ value: 1 })
      .mockResolvedValueOnce({ value: 2 })
      .mockResolvedValueOnce({ value: 3 });

    render(<Probe intervalMs={60_000} />);
    await act(async () => undefined);
    expect(screen.getByText("1")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(screen.getByText("2")).toBeVisible();

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(api).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(screen.getByText("3")).toBeVisible();
  });
});
