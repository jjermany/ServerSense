import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import SensePage from "./SensePage";

vi.mock("../api", () => ({ api: vi.fn() }));

describe("SENSE requests", () => {
  beforeEach(() => {
    vi.mocked(api).mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("stops the active backend request without saving a partial answer", async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi.fn((path: string | URL | Request, init?: RequestInit) => {
      if (String(path).includes("/api/ai/requests/")) {
        return Promise.resolve(
          new Response(JSON.stringify({ cancelled: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      let streamController: ReadableStreamDefaultController<Uint8Array>;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller;
          controller.enqueue(
            encoder.encode(
              'event: activity\ndata: {"message":"Checking storage…","request_id":"request-123"}\n\n',
            ),
          );
        },
      });
      init?.signal?.addEventListener("abort", () => {
        streamController.error(new DOMException("Stopped", "AbortError"));
      });
      return Promise.resolve(
        new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SensePage />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "How long until I run out of storage?",
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Stop response" }));

    expect(await screen.findByText("Request stopped.")).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/ai/requests/request-123",
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });

  it("deletes a conversation and clears it when active", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.mocked(api).mockImplementation((path, options) => {
      if (path === "/api/ai/conversations/7" && !options?.method) {
        return Promise.resolve({
          id: 7,
          messages: [{ role: "user", content: "Old question" }],
        });
      }
      if (path === "/api/ai/conversations") {
        return Promise.resolve([
          { id: 7, title: "Storage forecast", updated_at: "2026-08-26T00:00:00Z" },
        ]);
      }
      return Promise.resolve(undefined);
    });

    render(<SensePage />);
    fireEvent.click(
      await screen.findByRole("button", { name: /^Storage forecast/i }),
    );
    expect(await screen.findByText("Old question")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Delete conversation: Storage forecast" }),
    );

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith("/api/ai/conversations/7", { method: "DELETE" }),
    );
    expect(await screen.findByText("What would you like to understand?")).toBeInTheDocument();
  });
});
