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
  proactive_insights: false,
};

const alertConfig = {
  free_percent_threshold: 10,
  forecast_days_threshold: 90,
  temperature_c_threshold: 50,
  webhook_enabled: false,
  webhook_configured: false,
};

describe("AI settings", () => {
  beforeEach(() => {
    vi.mocked(api).mockImplementation((path: string) =>
      Promise.resolve(path.endsWith("/alerts") ? alertConfig : aiConfig),
    );
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
      expect(payload.max_tool_calls).toBe(5);
      expect(payload.timeout_seconds).toBe(30);
    });
  });
});
