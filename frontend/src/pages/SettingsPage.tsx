import { FormEvent, useEffect, useState } from "react";
import {
  Bell,
  Bot,
  CheckCircle2,
  Database,
  Download,
  Plug,
  Save,
  SlidersHorizontal,
} from "lucide-react";
import { api } from "../api";
import { Card, PageHeader } from "../components/UI";
type AIConfig = {
  provider: string;
  model: string;
  endpoint: string;
  api_key_configured: boolean;
  context_window: number;
  temperature: number;
  timeout_seconds: number;
  max_tool_calls: number;
  proactive_insights: boolean;
};
type AlertConfig = {
  free_percent_threshold: number;
  forecast_days_threshold: number;
  temperature_c_threshold: number;
  webhook_enabled: boolean;
  webhook_configured: boolean;
};
type GeneralConfig = {
  server_name?: string;
  demo_mode?: boolean;
};
type IntegrationsConfig = {
  available_providers: Array<{
    key: string;
    name: string;
    description: string;
    read_only: boolean;
  }>;
  configured: Array<{
    id: number;
    provider: string;
    name: string;
    enabled: boolean;
    configured: boolean;
  }>;
};
export default function SettingsPage() {
  const [config, setConfig] = useState<AIConfig>();
  const [alerts, setAlerts] = useState<AlertConfig>();
  const [general, setGeneral] = useState<GeneralConfig>();
  const [integrations, setIntegrations] = useState<IntegrationsConfig>();
  const [message, setMessage] = useState("");
  const [testing, setTesting] = useState(false);
  useEffect(() => {
    Promise.all([
      api<AIConfig>("/api/settings/ai"),
      api<AlertConfig>("/api/settings/alerts"),
      api<GeneralConfig>("/api/settings/general"),
      api<IntegrationsConfig>("/api/integrations"),
    ]).then(([ai, alertConfig, generalConfig, integrationsConfig]) => {
      setConfig(ai);
      setAlerts(alertConfig);
      setGeneral(generalConfig);
      setIntegrations(integrationsConfig);
    });
  }, []);
  if (!config || !alerts || !general || !integrations)
    return <div className="page" />;
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const raw = Object.fromEntries(form);
    await api("/api/settings/ai", {
      method: "PUT",
      body: JSON.stringify({
        ...raw,
        context_window: Number(raw.context_window),
        temperature: Number(raw.temperature),
        timeout_seconds: Number(raw.timeout_seconds),
        max_tool_calls: Number(raw.max_tool_calls),
        proactive_insights: form.get("proactive_insights") === "on",
      }),
    });
    setMessage("AI settings saved securely.");
    setTimeout(() => setMessage(""), 3000);
  };
  const test = async () => {
    setTesting(true);
    try {
      const result = await api<{ detail: string }>("/api/settings/ai/test", {
        method: "POST",
      });
      setMessage(result.detail);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Test failed");
    } finally {
      setTesting(false);
    }
  };
  const saveAlerts = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const raw = Object.fromEntries(form);
    const updated = await api<AlertConfig>("/api/settings/alerts", {
      method: "PUT",
      body: JSON.stringify({
        free_percent_threshold: Number(raw.free_percent_threshold),
        forecast_days_threshold: Number(raw.forecast_days_threshold),
        temperature_c_threshold: Number(raw.temperature_c_threshold),
        webhook_enabled: alerts.webhook_enabled,
      }),
    });
    setAlerts(updated);
    setMessage("Alert settings saved securely.");
    setTimeout(() => setMessage(""), 3000);
  };
  const saveWebhook = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const updated = await api<AlertConfig>("/api/settings/alerts", {
      method: "PUT",
      body: JSON.stringify({
        free_percent_threshold: alerts.free_percent_threshold,
        forecast_days_threshold: alerts.forecast_days_threshold,
        temperature_c_threshold: alerts.temperature_c_threshold,
        webhook_enabled: form.get("webhook_enabled") === "on",
        webhook_url: form.get("webhook_url"),
      }),
    });
    setAlerts(updated);
    setMessage("Integration settings saved securely.");
    setTimeout(() => setMessage(""), 3000);
  };
  const testWebhook = async () => {
    setTesting(true);
    try {
      const result = await api<{ detail: string }>(
        "/api/settings/alerts/test",
        {
          method: "POST",
        },
      );
      setMessage(result.detail);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Test failed");
    } finally {
      setTesting(false);
    }
  };
  const createBackup = async () => {
    const result = await api<{ filename: string }>("/api/system/backup", {
      method: "POST",
    });
    setMessage(`Backup created: ${result.filename}`);
  };
  return (
    <div className="page">
      <PageHeader eyebrow="CONFIGURATION" title="Settings" />
      {message && <div className="save-message">{message}</div>}
      <div className="settings-layout">
        <aside>
          <a className="active" href="#ai">
            <Bot />
            AI
          </a>
          <a href="#alerts">
            <Bell />
            Alerts
          </a>
          <a href="#monitoring">
            <SlidersHorizontal />
            Monitoring
          </a>
          <a href="#integrations">
            <Plug />
            Integrations
          </a>
          <a href="#advanced">
            <Database />
            Advanced
          </a>
        </aside>
        <div className="settings-stack">
          <Card className="settings-card">
            <span id="ai" />
            <div className="settings-title">
              <span>
                <Bot />
              </span>
              <div>
                <h2>SENSE AI</h2>
                <p>
                  Choose the model that powers SENSE. Monitoring and
                  deterministic insights work without one.
                </p>
              </div>
            </div>
            <form onSubmit={submit}>
              <div className="field-grid">
                <label>
                  Provider
                  <select name="provider" defaultValue={config.provider}>
                    <option value="disabled">
                      Built-in deterministic mode
                    </option>
                    <option value="ollama">Ollama-compatible</option>
                    <option value="openai_compatible">
                      OpenAI-compatible API
                    </option>
                  </select>
                </label>
                <label>
                  Model
                  <input
                    name="model"
                    defaultValue={config.model}
                    placeholder="e.g. llama3.2:3b"
                  />
                </label>
              </div>
              <label>
                Endpoint
                <input
                  name="endpoint"
                  type="url"
                  defaultValue={config.endpoint}
                  placeholder="http://host.docker.internal:11434"
                />
                <small>
                  ServerSense adds /v1/chat/completions when sending requests.
                </small>
              </label>
              <label>
                API key
                <input
                  name="api_key"
                  type="password"
                  placeholder={
                    config.api_key_configured
                      ? "Configured — leave blank to keep"
                      : "Optional for local endpoints"
                  }
                  autoComplete="new-password"
                />
              </label>
              <div className="field-grid three">
                <label>
                  Context window
                  <input
                    name="context_window"
                    type="number"
                    min="1024"
                    defaultValue={config.context_window}
                  />
                </label>
                <label>
                  Temperature
                  <input
                    name="temperature"
                    type="number"
                    min="0"
                    max="2"
                    step="0.1"
                    defaultValue={config.temperature}
                  />
                </label>
                <label>
                  Max tool calls
                  <input
                    name="max_tool_calls"
                    type="number"
                    min="1"
                    max="12"
                    defaultValue={config.max_tool_calls}
                  />
                </label>
              </div>
              <label>
                Timeout (seconds)
                <input
                  name="timeout_seconds"
                  type="number"
                  min="5"
                  max="600"
                  defaultValue={config.timeout_seconds}
                />
              </label>
              <label className="check">
                <input
                  name="proactive_insights"
                  type="checkbox"
                  defaultChecked={config.proactive_insights}
                />
                <span>
                  <b>Explain new alerts with SENSE</b>
                  <small>
                    Send each new deterministic alert batch to the configured
                    model once for a concise explanation. Monitoring and alerts
                    continue normally if the model is unavailable.
                  </small>
                </span>
              </label>
              <div className="permission-box">
                <CheckCircle2 />
                <div>
                  <b>Read-only policy enforced</b>
                  <p>
                    SENSE can query ServerSense’s allowlisted telemetry tools.
                    It cannot execute shell commands, change Docker, delete
                    files, or modify Unraid.
                  </p>
                </div>
              </div>
              <div className="form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={test}
                  disabled={testing}
                >
                  {testing ? "Testing…" : "Test model"}
                </button>
                <button className="primary">
                  <Save size={16} />
                  Save settings
                </button>
              </div>
            </form>
          </Card>
          <Card className="settings-card">
            <span id="alerts" />
            <div className="settings-title">
              <span>
                <Bell />
              </span>
              <div>
                <h2>ALERT THRESHOLDS</h2>
                <p>
                  Set the deterministic thresholds used to create alerts.
                </p>
              </div>
            </div>
            <form onSubmit={saveAlerts}>
              <div className="field-grid three">
                <label>
                  Free storage threshold (%)
                  <input
                    name="free_percent_threshold"
                    type="number"
                    min="1"
                    max="50"
                    defaultValue={alerts.free_percent_threshold}
                  />
                </label>
                <label>
                  Forecast threshold (days)
                  <input
                    name="forecast_days_threshold"
                    type="number"
                    min="1"
                    max="3650"
                    defaultValue={alerts.forecast_days_threshold}
                  />
                </label>
                <label>
                  Disk temperature (°C)
                  <input
                    name="temperature_c_threshold"
                    type="number"
                    min="30"
                    max="90"
                    defaultValue={alerts.temperature_c_threshold}
                  />
                </label>
              </div>
              <div className="form-actions">
                <button className="primary">
                  <Save size={16} />
                  Save thresholds
                </button>
              </div>
            </form>
          </Card>
          <Card className="settings-card">
            <span id="monitoring" />
            <div className="settings-title">
              <span>
                <SlidersHorizontal />
              </span>
              <div>
                <h2>MONITORING</h2>
                <p>Review the collection mode selected during first-launch setup.</p>
              </div>
            </div>
            <div className="settings-summary">
              <div>
                <small>SERVER</small>
                <b>{general.server_name || "ServerSense Host"}</b>
              </div>
              <div>
                <small>COLLECTION MODE</small>
                <b>{general.demo_mode ? "Demo data" : "Live monitoring"}</b>
              </div>
            </div>
            <div className="permission-box">
              <CheckCircle2 />
              <div>
                <b>Monitoring is independent of SENSE AI</b>
                <p>
                  Collection, forecasts, and deterministic alerts continue when
                  the AI provider is disabled or unavailable.
                </p>
              </div>
            </div>
            <p className="settings-note">
              Monitoring mode is locked after setup so demo and live telemetry
              cannot be mixed in the same database.
            </p>
          </Card>
          <Card className="settings-card">
            <span id="integrations" />
            <div className="settings-title">
              <span>
                <Plug />
              </span>
              <div>
                <h2>INTEGRATIONS</h2>
                <p>Connect alert delivery and installed read-only providers.</p>
              </div>
            </div>
            <form onSubmit={saveWebhook}>
              <label className="check">
                <input
                  name="webhook_enabled"
                  type="checkbox"
                  defaultChecked={alerts.webhook_enabled}
                />
                <span>
                  <b>Enable generic webhook</b>
                  <small>Send each newly created alert as structured JSON.</small>
                </span>
              </label>
              <label>
                Webhook URL
                <input
                  name="webhook_url"
                  type="url"
                  placeholder={
                    alerts.webhook_configured
                      ? "Configured — leave blank to keep"
                      : "https://example.com/webhook"
                  }
                />
              </label>
              {integrations.available_providers.map((provider) => (
                <div className="integration-provider" key={provider.key}>
                  <span>
                    <b>{provider.name}</b>
                    <small>{provider.description}</small>
                  </span>
                  <small>{provider.read_only ? "Read only" : "Installed"}</small>
                </div>
              ))}
              <div className="form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={testWebhook}
                  disabled={testing}
                >
                  Test webhook
                </button>
                <button className="primary">
                  <Save size={16} />
                  Save integration
                </button>
              </div>
            </form>
          </Card>
          <Card className="settings-card">
            <span id="advanced" />
            <div className="settings-title">
              <span>
                <Database />
              </span>
              <div>
                <h2>ADVANCED & DIAGNOSTICS</h2>
                <p>
                  Create a consistent SQLite backup or download a sanitized
                  diagnostic bundle.
                </p>
              </div>
            </div>
            <div className="advanced-actions">
              <button className="secondary" onClick={createBackup}>
                <Database size={16} />
                Create backup
              </button>
              <a className="secondary" href="/api/system/diagnostics">
                <Download size={16} />
                Download diagnostics
              </a>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
