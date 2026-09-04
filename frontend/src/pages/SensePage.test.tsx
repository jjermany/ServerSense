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
    let backgrounded = false;
    vi.mocked(api).mockImplementation((path) => {
      if (path === "/api/ai/jobs/job-456/notification") {
        return Promise.resolve({ id: "job-456", notify_on_completion: false });
      }
      if (path === "/api/ai/jobs") {
        return Promise.resolve(backgrounded ? [{
          id: "job-456",
          conversation_id: 4,
          status: "streaming",
          model: "test-model",
          partial_response: "",
          backgrounded: true,
          notify_on_completion: true,
        }] : []);
      }
      if (path === "/api/ai/notifications") {
        return Promise.resolve({ unread: 0, items: [] });
      }
      return Promise.resolve([]);
    });
    const fetchMock = vi.fn((_path: string | URL | Request, init?: RequestInit) => {
      requestSignal = init?.signal;
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller;
          backgrounded = true;
          controller.enqueue(
            encoder.encode(
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
    const notifyControls = screen.getAllByRole("checkbox", { name: "Notify me when complete" });
    expect(notifyControls).toHaveLength(1);
    const [notify] = notifyControls;
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

  it("hides a superseded failure after its retry is accepted", async () => {
    const failedJob = {
      id: "failed-job",
      conversation_id: 7,
      user_message_id: 42,
      status: "failed",
      model: "test-model",
      partial_response: "",
      backgrounded: false,
      notify_on_completion: true,
      error: "The provider timed out.",
    };
    const retriedJob = {
      ...failedJob,
      id: "retry-job",
      status: "analyzing",
      error: "",
    };
    let jobRows = [failedJob];
    vi.mocked(api).mockImplementation((path, options) => {
      if (path === "/api/ai/conversations") {
        return Promise.resolve([
          { id: 7, title: "Server changes", updated_at: "2026-09-04T12:00:00Z" },
        ]);
      }
      if (path === "/api/ai/conversations/7") {
        return Promise.resolve({
          id: 7,
          messages: [{ id: 42, role: "user", content: "What changed?", source: "user" }],
        });
      }
      if (path === "/api/ai/jobs/failed-job/retry" && options?.method === "POST") {
        jobRows = [retriedJob, failedJob];
        return Promise.resolve(retriedJob);
      }
      if (path === "/api/ai/jobs") return Promise.resolve(jobRows);
      if (path === "/api/ai/notifications") {
        return Promise.resolve({ unread: 0, items: [] });
      }
      return Promise.resolve([]);
    });

    render(<SensePage />);
    fireEvent.click(await screen.findByRole("button", { name: /^Server changes/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

    expect(await screen.findByText(/SENSE AI .* analyzing/)).toBeInTheDocument();
    expect(screen.queryByText("The provider timed out.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("pins elapsed time to ongoing and completed SENSE messages", async () => {
    const startedAt = new Date(Date.now() - 65_000).toISOString();
    vi.mocked(api).mockImplementation((path) => {
      if (path === "/api/ai/conversations") {
        return Promise.resolve([
          { id: 8, title: "Timing", updated_at: "2026-09-04T12:00:00Z" },
        ]);
      }
      if (path === "/api/ai/conversations/8") {
        return Promise.resolve({
          id: 8,
          messages: [
            { id: 80, role: "user", content: "First question", source: "user" },
            {
              id: 81,
              role: "assistant",
              content: "Completed answer",
              source: "sense_ai",
              model: "test-model",
              elapsed_seconds: 12,
            },
          ],
        });
      }
      if (path === "/api/ai/jobs") {
        return Promise.resolve([
          {
            id: "active-job",
            conversation_id: 8,
            user_message_id: 82,
            status: "analyzing",
            model: "test-model",
            partial_response: "",
            backgrounded: false,
            notify_on_completion: true,
            started_at: startedAt,
          },
        ]);
      }
      if (path === "/api/ai/notifications") {
        return Promise.resolve({ unread: 0, items: [] });
      }
      return Promise.resolve([]);
    });

    render(<SensePage />);
    fireEvent.click(await screen.findByRole("button", { name: /^Timing/i }));

    expect(await screen.findByText(/SENSE AI .* Elapsed 12s/)).toBeInTheDocument();
    expect(screen.getByText(/Elapsed 1m [4-7]s/)).toBeInTheDocument();
  });

  it("collapses conversation history behind a mobile-friendly toggle", async () => {
    vi.mocked(api).mockImplementation((path) => {
      if (path === "/api/ai/conversations") {
        return Promise.resolve([
          { id: 7, title: "Storage forecast", updated_at: "2026-08-26T00:00:00Z" },
        ]);
      }
      if (path === "/api/ai/notifications") {
        return Promise.resolve({ unread: 0, items: [] });
      }
      return Promise.resolve([]);
    });

    render(<SensePage />);
    const toggle = await screen.findByRole("button", { name: /CONVERSATIONS/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("dismisses individual notifications", async () => {
    let notices = [{ id: 12, title: "Analysis complete", preview: "Ready", read_at: null }];
    vi.mocked(api).mockImplementation((path, options) => {
      if (path === "/api/ai/notifications" && options?.method === "DELETE") {
        notices = [];
        return Promise.resolve(undefined);
      }
      if (path === "/api/ai/notifications/12" && options?.method === "DELETE") {
        notices = [];
        return Promise.resolve(undefined);
      }
      if (path === "/api/ai/notifications") {
        return Promise.resolve({ unread: notices.length, items: notices });
      }
      return Promise.resolve([]);
    });

    render(<SensePage />);
    fireEvent.click(await screen.findByRole("button", { name: /Notifications/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss notification: Analysis complete" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/ai/notifications/12", { method: "DELETE" }));
    expect(screen.queryByText("Analysis complete")).not.toBeInTheDocument();
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
