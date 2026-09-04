import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChunkErrorBoundary from "./ChunkErrorBoundary";

function Bomb({ message }: { message: string }): never {
  throw new Error(message);
}

describe("ChunkErrorBoundary", () => {
  const originalLocation = window.location;

  beforeEach(() => {
    // jsdom throws on real navigation; replace location with a spyable stub.
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...originalLocation,
        href: "https://server.local/",
        search: "",
        assign: vi.fn(),
        reload: vi.fn(),
      },
    });
  });

  afterEach(() => {
    cleanup();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
    vi.restoreAllMocks();
  });

  it("reloads automatically once when a lazy chunk fails to load", () => {
    render(
      <ChunkErrorBoundary>
        <Bomb message="Failed to fetch dynamically imported module: /assets/DashboardPage-abc123.js" />
      </ChunkErrorBoundary>,
    );

    expect(window.location.assign).toHaveBeenCalledTimes(1);
    const [reloadUrl] = vi.mocked(window.location.assign).mock.calls[0];
    expect(String(reloadUrl)).toContain("_ssretry=");
    expect(
      screen.getByText(/newer version of ServerSense/i),
    ).toBeInTheDocument();
  });

  it("does not auto-reload a second time for the same stale build", () => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...originalLocation,
        href: "https://server.local/?_ssretry=1700000000000",
        search: "?_ssretry=1700000000000",
        assign: vi.fn(),
        reload: vi.fn(),
      },
    });

    render(
      <ChunkErrorBoundary>
        <Bomb message="Failed to fetch dynamically imported module: /assets/DashboardPage-abc123.js" />
      </ChunkErrorBoundary>,
    );

    expect(window.location.assign).not.toHaveBeenCalled();
    expect(
      screen.getByText(/still couldn't load after reloading/i),
    ).toBeInTheDocument();
  });

  it("shows a generic reload prompt for a non-chunk error without auto-reloading", () => {
    render(
      <ChunkErrorBoundary>
        <Bomb message="Cannot read properties of undefined" />
      </ChunkErrorBoundary>,
    );

    expect(window.location.assign).not.toHaveBeenCalled();
    expect(screen.getByText("This page failed to load.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload now" })).toBeInTheDocument();
  });
});
