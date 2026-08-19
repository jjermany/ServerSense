import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import SettingsPage from "./SettingsPage";

vi.mock("../api", () => ({ api: vi.fn() }));

const aiConfig = {
  provider: "openai_compatible",
  model: "local-model",
  endpoint: "http://local-model.test",
  api_key_configured: false,
  context_window: 8192,
  temperature: 0.2,
  timeout_seconds: 30,
  max_tool_calls: 5,
  max_output_tokens: 512,
  proactive_insights: false,
  dashboard_summaries: false,
};

const alertConfig = {
  free_percent_threshold: 10,
  forecast_days_threshold: 90,
  temperature_c_threshold: 50,
  notify_storage_low: true,
  notify_forecast_low: true,
  notify_disk_smart: true,
  notify_disk_temperature: true,
  notify_container_stopped: true,
  webhook_enabled: false,
  webhook_configured: false,
  discord_enabled: false,
  discord_webhook_url_configured: false,
  pushover_enabled: false,
  pushover_user_key_configured: false,
  pushover_app_token_configured: false,
  email_enabled: false,
  smtp_host: "",
  smtp_port: 587,
  smtp_security: "starttls" as const,
  smtp_username_configured: false,
  smtp_password_configured: false,
  email_from: "",
  email_to: "",
};

const generalConfig = { server_name: "Test Tower", demo_mode: false };
const integrationsConfig = { available_providers: [], configured: [] };

describe("AI settings", () => {
  beforeEach(() => {
    vi.mocked(api).mockImplementation((path: string) => {
      if (path.endsWith("/alerts")) return Promise.resolve(alertConfig);
      if (path.endsWith("/general")) return Promise.resolve(generalConfig);
      if (path === "/api/integrations")
        return Promise.resolve(integrationsConfig);
      return Promise.resolve(aiConfig);
    });
  });
  afterEach(cleanup);

  it("requires an explicit opt-in for proactive model explanations", async () => {
    render(<SettingsPage />);
    const optIn = await screen.findByLabelText(/Explain new alerts with SENSE/);
    expect(optIn).not.toBeChecked();

    fireEvent.click(optIn);
    fireEvent.submit(optIn.closest("form")!);

    await waitFor(() => {
      const update = vi
        .mocked(api)
        .mock.calls.find(
          ([path, options]) =>
            path === "/api/settings/ai" && options?.method === "PUT",
        );
      expect(update).toBeDefined();
      const payload = JSON.parse(String(update?.[1]?.body));
      expect(payload.proactive_insights).toBe(true);
      expect(payload.dashboard_summaries).toBe(false);
      expect(payload.max_tool_calls).toBe(5);
      expect(payload.max_output_tokens).toBe(512);
      expect(payload.timeout_seconds).toBe(30);
    });
  });

  it("offers a separate opt-in for cached dashboard summaries", async () => {
    render(<SettingsPage />);
    const summary = await screen.findByLabelText(/Add a cached AI dashboard summary/);
    expect(summary).not.toBeChecked();

    fireEvent.click(summary);
    fireEvent.submit(summary.closest("form")!);

    await waitFor(() => {
      const update = vi
        .mocked(api)
        .mock.calls.find(
          ([path, options]) =>
            path === "/api/settings/ai" &&
            options?.method === "PUT" &&
            JSON.parse(String(options.body)).dashboard_summaries === true,
        );
      expect(update).toBeDefined();
    });
  });

  it("links monitoring and integrations to working settings sections", async () => {
    render(<SettingsPage />);

    expect(await screen.findByRole("link", { name: /Monitoring/ })).toHaveAttribute(
      "href",
      "#monitoring",
    );
    expect(screen.getByRole("link", { name: /Integrations/ })).toHaveAttribute(
      "href",
      "#integrations",
    );
    expect(screen.getByText("Live monitoring")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save integration/ })).toBeInTheDocument();
    expect(screen.getByText("Discord")).toBeInTheDocument();
    expect(screen.getByText("Pushover")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument();
  });

  it("shows progress and success next to a save action", async () => {
    let finishSave: ((value: unknown) => void) | undefined;
    vi.mocked(api).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/settings/ai" && options?.method === "PUT") {
        return new Promise((resolve) => {
          finishSave = resolve;
        });
      }
      if (path.endsWith("/alerts")) return Promise.resolve(alertConfig);
      if (path.endsWith("/general")) return Promise.resolve(generalConfig);
      if (path === "/api/integrations") return Promise.resolve(integrationsConfig);
      return Promise.resolve(aiConfig);
    });

    render(<SettingsPage />);
    const save = await screen.findByRole("button", { name: "Save settings" });
    fireEvent.click(save);

    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    finishSave?.(aiConfig);

    expect(
      await screen.findByRole("status", { name: "" }),
    ).toHaveTextContent("AI settings saved securely.");
    expect(screen.getByRole("button", { name: "Save settings" })).toBeEnabled();
  });

  it("shows notification test progress and errors at the clicked provider", async () => {
    vi.mocked(api).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/settings/alerts/test/webhook" && options?.method === "POST") {
        return Promise.reject(new Error("Webhook delivery failed"));
      }
      if (path.endsWith("/alerts")) return Promise.resolve(alertConfig);
      if (path.endsWith("/general")) return Promise.resolve(generalConfig);
      if (path === "/api/integrations") return Promise.resolve(integrationsConfig);
      return Promise.resolve(aiConfig);
    });

    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Test Webhook" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Webhook delivery failed",
    );
    expect(screen.getByRole("button", { name: "Test Webhook" })).toBeEnabled();
  });

  it("saves the forecast lead time and notification categories", async () => {
    render(<SettingsPage />);

    const forecast = await screen.findByLabelText(
      "Notify before projected exhaustion (days)",
    );
    fireEvent.change(forecast, { target: { value: "180" } });
    fireEvent.click(screen.getByLabelText("Projected storage exhaustion"));
    fireEvent.submit(forecast.closest("form")!);

    await waitFor(() => {
      const update = vi
        .mocked(api)
        .mock.calls.find(
          ([path, options]) =>
            path === "/api/settings/alerts" && options?.method === "PUT",
        );
      expect(update).toBeDefined();
      const payload = JSON.parse(String(update?.[1]?.body));
      expect(payload.forecast_days_threshold).toBe(180);
      expect(payload.notify_forecast_low).toBe(false);
      expect(payload.notify_storage_low).toBe(true);
      expect(payload.notify_container_stopped).toBe(true);
    });
  });

  it("adds multiple independently named Radarr instances", async () => {
    vi.mocked(api).mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/integrations" && options?.method === "POST") {
        return Promise.resolve({ id: 9 });
      }
      if (path === "/api/integrations") return Promise.resolve(integrationsConfig);
      if (path.endsWith("/alerts")) return Promise.resolve(alertConfig);
      if (path.endsWith("/general")) return Promise.resolve(generalConfig);
      return Promise.resolve(aiConfig);
    });
    render(<SettingsPage />);

    fireEvent.change(await screen.findByPlaceholderText("Movies or Anime"), {
      target: { value: "Anime" },
    });
    fireEvent.change(screen.getByPlaceholderText("http://radarr:7878"), {
      target: { value: "http://radarr-anime:7878" },
    });
    const key = screen.getByLabelText("API key", {
      selector: '.media-integration-editor.add input',
    });
    fireEvent.change(key, { target: { value: "anime-secret" } });
    fireEvent.submit(key.closest("form")!);

    await waitFor(() => {
      const create = vi
        .mocked(api)
        .mock.calls.find(
          ([path, options]) =>
            path === "/api/integrations" && options?.method === "POST",
        );
      expect(create).toBeDefined();
      expect(JSON.parse(String(create?.[1]?.body))).toMatchObject({
        provider: "radarr",
        name: "Anime",
        url: "http://radarr-anime:7878",
        api_key: "anime-secret",
        enabled: true,
      });
    });
  });
});
