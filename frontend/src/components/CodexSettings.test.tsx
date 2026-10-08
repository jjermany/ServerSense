import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { TimeZoneContext } from "../timeZoneContext";
import CodexSettings from "./CodexSettings";

vi.mock("../api", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const disconnected = { signed_in: false, plan: null, login: null, limits: [], measured_at: "2030-01-01T00:00:00Z" };

describe("Codex account settings", () => {
  it("starts and cancels device login without submitting the AI settings form", async () => {
    vi.mocked(api).mockImplementation((path, options) => {
      if (path.endsWith("/login") && options?.method === "POST") return Promise.resolve({ state: "pending", verification_url: "https://auth.openai.com/codex/device", user_code: "TEST-1234" });
      if (options?.method === "DELETE") return Promise.resolve({ ok: true });
      return Promise.resolve(disconnected);
    });
    const submit = vi.fn((event) => event.preventDefault());
    render(<form onSubmit={submit}><CodexSettings /></form>);
    await screen.findByText("ChatGPT is not connected.");
    fireEvent.click(screen.getByRole("button", { name: "Sign in with ChatGPT" }));
    expect(await screen.findByText("TEST-1234")).toBeVisible();
    expect(screen.getByRole("link", { name: "ChatGPT device sign-in" })).toHaveAttribute("href", "https://auth.openai.com/codex/device");
    expect(submit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel sign-in" }));
    await screen.findByRole("button", { name: "Sign in with ChatGPT" });
    expect(screen.queryByText("TEST-1234")).not.toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/api/settings/ai/codex/login", { method: "DELETE" });
  });

  it("shows subscription exhaustion and reset times in the configured timezone", async () => {
    vi.mocked(api).mockResolvedValue({ ...disconnected, signed_in: true, plan: "plus", login: { state: "signed_in" }, limits: [{ bucket: "codex", window: "primary", used_percent: 100, window_minutes: 300, resets_at: "2030-01-01T00:00:00Z", exhausted: true }] });
    render(<TimeZoneContext.Provider value={{ timeZone: "America/Chicago", setTimeZone: () => undefined }}><CodexSettings /></TimeZoneContext.Provider>);
    expect(await screen.findByText("ChatGPT connected (plus)")).toBeVisible();
    const usage = screen.getByLabelText("Codex subscription usage");
    expect(usage).toHaveTextContent("100% used");
    expect(usage).toHaveTextContent("exhausted");
    expect(usage).toHaveTextContent("6:00 PM");
    expect(usage).not.toHaveTextContent("00:00:00");
  });

  it("keeps a visible retry after account loading fails", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Codex runtime unavailable"));
    render(<CodexSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Codex runtime unavailable");
    vi.mocked(api).mockResolvedValue(disconnected);
    fireEvent.click(screen.getByRole("button", { name: "Refresh account and usage" }));
    await screen.findByText("ChatGPT is not connected.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
