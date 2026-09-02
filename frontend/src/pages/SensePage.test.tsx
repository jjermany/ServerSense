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

  it("keeps streaming after the long-running threshold and allows notification control", async () => {
    const encoder = new TextEncoder();
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    let requestSignal: AbortSignal | null | undefined;
    vi.mocked(api).mockImplementation((path) => {
      if (path === "/api/ai/jobs/job-456/notification") {
        return Promise.resolve({ id: "job-456", notify_on_completion: false });
      }
      return Promise.resolve([]);
    });
    const fetchMock = vi.fn((_path: string | URL | Request, init?: RequestInit) => {
      requestSignal = init?.signal;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller;
          controller.enqueue(
            encoder.encode(
              'event: status\ndata: {"message":"","request_id":"job-456","status":"analyzing"}\n\n' +
                'event: backgrounded\ndata: {"message":"SENSE AI is still working.","job_id":"job-456","notify_on_completion":true}\n\n',
            ),
          );
        },
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

    expect((await screen.findAllByText("SENSE AI is still working.")).length).toBeGreaterThan(0);
    expect(requestSignal?.aborted).toBe(false);
    const notify = screen.getByRole("checkbox", { name: "Notify me when complete" });
    fireEvent.click(notify);
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith("/api/ai/jobs/job-456/notification", {
        method: "PATCH",
        body: JSON.stringify({ notify_on_completion: false }),
      }),
    );

    if (!streamController) throw new Error("Expected the response stream to be initialized");
    streamController.enqueue(
      encoder.encode(
        'event: message\ndata: {"message":"Finished after the threshold.","job_id":"job-456","source":"sense_ai","model":"test-model"}\n\n',
      ),
    );
    streamController.close();
    expect(await screen.findByText("Finished after the threshold.")).toBeInTheDocument();
    expect(requestSignal?.aborted).toBe(false);
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
