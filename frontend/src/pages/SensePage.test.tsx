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
});
