import { FormEvent, useEffect, useState } from "react";
import {
  Bell,
  Bot,
  CheckCircle2,
  Database,
  Download,
  LoaderCircle,
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
  discord_enabled: boolean;
  discord_webhook_url_configured: boolean;
  pushover_enabled: boolean;
  pushover_user_key_configured: boolean;
  pushover_app_token_configured: boolean;
  email_enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_security: "starttls" | "tls" | "none";
  smtp_username_configured: boolean;
  smtp_password_configured: boolean;
  email_from: string;
  email_to: string;
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
const alertProviderValues = (alerts: AlertConfig) => ({
  webhook_enabled: alerts.webhook_enabled,
  discord_enabled: alerts.discord_enabled,
  pushover_enabled: alerts.pushover_enabled,
  email_enabled: alerts.email_enabled,
  smtp_host: alerts.smtp_host,
  smtp_port: alerts.smtp_port,
  smtp_security: alerts.smtp_security,
  email_from: alerts.email_from,
  email_to: alerts.email_to,
});
const secretPlaceholder = (configured: boolean, empty: string) =>
  configured ? "Configured — leave blank to keep" : empty;

type ActionStatus = {
  phase: "pending" | "success" | "error";
  message: string;
};

function ActionFeedback({ status }: { status?: ActionStatus }) {
  if (!status || status.phase === "pending") return null;
  return (
    <div
      className={`action-feedback ${status.phase}`}
      role={status.phase === "error" ? "alert" : "status"}
      aria-live="polite"
    >
      {status.message}
    </div>
  );
}

function TestNotificationButton({
  label,
  status,
  onTest,
}: {
  label: string;
  status?: ActionStatus;
  onTest: (provider: string) => Promise<void>;
}) {
  const providerName = label.charAt(0).toUpperCase() + label.slice(1);
  return (
    <div className="notification-test-action">
      <button
        type="button"
        className="secondary notification-test"
        onClick={() => void onTest(label)}
        disabled={status?.phase === "pending"}
      >
        {status?.phase === "pending" && (
          <LoaderCircle className="spin" size={16} />
        )}
        {status?.phase === "pending"
          ? `Testing ${providerName}…`
          : `Test ${providerName}`}
      </button>
      <ActionFeedback status={status} />
    </div>
  );
}
export default function SettingsPage() {
  const [config, setConfig] = useState<AIConfig>();
  const [alerts, setAlerts] = useState<AlertConfig>();
  const [general, setGeneral] = useState<GeneralConfig>();
  const [integrations, setIntegrations] = useState<IntegrationsConfig>();
  const [actions, setActions] = useState<Record<string, ActionStatus>>({});
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
  const runAction = async <T,>(
    key: string,
    pendingMessage: string,
    request: () => Promise<T>,
    successMessage: string | ((result: T) => string),
  ) => {
    setActions((current) => ({
      ...current,
      [key]: { phase: "pending", message: pendingMessage },
    }));
    try {
      const result = await request();
      setActions((current) => ({
        ...current,
        [key]: {
          phase: "success",
          message:
            typeof successMessage === "function"
              ? successMessage(result)
              : successMessage,
        },
      }));
    } catch (error) {
      setActions((current) => ({
        ...current,
        [key]: {
          phase: "error",
          message: error instanceof Error ? error.message : "Request failed",
        },
      }));
    }
  };
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const raw = Object.fromEntries(form);
    await runAction(
      "ai-save",
      "Saving AI settings…",
      () =>
        api("/api/settings/ai", {
          method: "PUT",
          body: JSON.stringify({
            ...raw,
            context_window: Number(raw.context_window),
            temperature: Number(raw.temperature),
            timeout_seconds: Number(raw.timeout_seconds),
            max_tool_calls: Number(raw.max_tool_calls),
            proactive_insights: form.get("proactive_insights") === "on",
          }),
        }),
      "AI settings saved securely.",
    );
  };
  const test = async () => {
    await runAction(
      "ai-test",
      "Testing model connection…",
      () => api<{ detail: string }>("/api/settings/ai/test", { method: "POST" }),
      (result) => result.detail,
    );
  };
  const saveAlerts = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const raw = Object.fromEntries(form);
    await runAction(
      "alerts-save",
      "Saving alert thresholds…",
      async () => {
        const updated = await api<AlertConfig>("/api/settings/alerts", {
          method: "PUT",
          body: JSON.stringify({
            ...alertProviderValues(alerts),
            free_percent_threshold: Number(raw.free_percent_threshold),
            forecast_days_threshold: Number(raw.forecast_days_threshold),
            temperature_c_threshold: Number(raw.temperature_c_threshold),
            webhook_enabled: alerts.webhook_enabled,
          }),
        });
        setAlerts(updated);
        return updated;
      },
      "Alert thresholds saved securely.",
    );
  };
  const saveWebhook = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    await runAction(
      "integrations-save",
      "Saving integrations…",
      async () => {
        const updated = await api<AlertConfig>("/api/settings/alerts", {
          method: "PUT",
          body: JSON.stringify({
        ...alertProviderValues(alerts),
        free_percent_threshold: alerts.free_percent_threshold,
        forecast_days_threshold: alerts.forecast_days_threshold,
        temperature_c_threshold: alerts.temperature_c_threshold,
        webhook_enabled: form.get("webhook_enabled") === "on",
        webhook_url: form.get("webhook_url"),
        discord_enabled: form.get("discord_enabled") === "on",
        discord_webhook_url: form.get("discord_webhook_url"),
        pushover_enabled: form.get("pushover_enabled") === "on",
        pushover_user_key: form.get("pushover_user_key"),
        pushover_app_token: form.get("pushover_app_token"),
        email_enabled: form.get("email_enabled") === "on",
        smtp_host: form.get("smtp_host"),
        smtp_port: Number(form.get("smtp_port")),
        smtp_security: form.get("smtp_security"),
        smtp_username: form.get("smtp_username"),
        smtp_password: form.get("smtp_password"),
        email_from: form.get("email_from"),
        email_to: form.get("email_to"),
          }),
        });
        setAlerts(updated);
        return updated;
      },
      "Integration settings saved securely.",
    );
  };
  const testNotification = async (provider: string) => {
    await runAction(
      `notification-test-${provider}`,
      `Testing ${provider}…`,
      () =>
        api<{ detail: string }>(`/api/settings/alerts/test/${provider}`, {
          method: "POST",
        }),
      (result) => result.detail,
    );
  };
  const createBackup = async () => {
    await runAction(
      "backup",
      "Creating backup…",
      () => api<{ filename: string }>("/api/system/backup", { method: "POST" }),
      (result) => `Backup created: ${result.filename}`,
    );
  };
  return (
    <div className="page">
      <PageHeader eyebrow="CONFIGURATION" title="Settings" />
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
              <div className="form-feedback">
                <ActionFeedback status={actions["ai-test"]} />
                <ActionFeedback status={actions["ai-save"]} />
              </div>
              <div className="form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={test}
                  disabled={actions["ai-test"]?.phase === "pending"}
                >
                  {actions["ai-test"]?.phase === "pending" && (
                    <LoaderCircle className="spin" size={16} />
                  )}
                  {actions["ai-test"]?.phase === "pending"
                    ? "Testing model…"
                    : "Test model"}
                </button>
                <button
                  className="primary"
                  disabled={actions["ai-save"]?.phase === "pending"}
                >
                  {actions["ai-save"]?.phase === "pending" ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Save size={16} />
                  )}
                  {actions["ai-save"]?.phase === "pending"
                    ? "Saving…"
                    : "Save settings"}
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
              <ActionFeedback status={actions["alerts-save"]} />
              <div className="form-actions">
                <button
                  className="primary"
                  disabled={actions["alerts-save"]?.phase === "pending"}
                >
                  {actions["alerts-save"]?.phase === "pending" ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Save size={16} />
                  )}
                  {actions["alerts-save"]?.phase === "pending"
                    ? "Saving…"
                    : "Save thresholds"}
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
              <div className="notification-option">
                <label className="check">
                  <input
                    name="webhook_enabled"
                    type="checkbox"
                    defaultChecked={alerts.webhook_enabled}
                  />
                  <span>
                    <b>Generic webhook</b>
                    <small>Send each alert as structured JSON.</small>
                  </span>
                </label>
                <label>
                  Webhook URL
                  <input
                    name="webhook_url"
                    type="url"
                    placeholder={secretPlaceholder(
                      alerts.webhook_configured,
                      "https://example.com/webhook",
                    )}
                  />
                </label>
                <TestNotificationButton
                  label="webhook"
                  status={actions["notification-test-webhook"]}
                  onTest={testNotification}
                />
              </div>
              <div className="notification-option">
                <label className="check">
                  <input
                    name="discord_enabled"
                    type="checkbox"
                    defaultChecked={alerts.discord_enabled}
                  />
                  <span>
                    <b>Discord</b>
                    <small>Post a formatted alert embed to a Discord channel.</small>
                  </span>
                </label>
                <label>
                  Discord webhook URL
                  <input
                    name="discord_webhook_url"
                    type="url"
                    placeholder={secretPlaceholder(
                      alerts.discord_webhook_url_configured,
                      "https://discord.com/api/webhooks/…",
                    )}
                  />
                </label>
                <TestNotificationButton
                  label="discord"
                  status={actions["notification-test-discord"]}
                  onTest={testNotification}
                />
              </div>
              <div className="notification-option">
                <label className="check">
                  <input
                    name="pushover_enabled"
                    type="checkbox"
                    defaultChecked={alerts.pushover_enabled}
                  />
                  <span>
                    <b>Pushover</b>
                    <small>Send alerts through your Pushover application.</small>
                  </span>
                </label>
                <div className="field-grid">
                  <label>
                    User key
                    <input
                      name="pushover_user_key"
                      type="password"
                      autoComplete="new-password"
                      placeholder={secretPlaceholder(
                        alerts.pushover_user_key_configured,
                        "Pushover user key",
                      )}
                    />
                  </label>
                  <label>
                    Application token
                    <input
                      name="pushover_app_token"
                      type="password"
                      autoComplete="new-password"
                      placeholder={secretPlaceholder(
                        alerts.pushover_app_token_configured,
                        "Application API token",
                      )}
                    />
                  </label>
                </div>
                <TestNotificationButton
                  label="pushover"
                  status={actions["notification-test-pushover"]}
                  onTest={testNotification}
                />
              </div>
              <div className="notification-option">
                <label className="check">
                  <input
                    name="email_enabled"
                    type="checkbox"
                    defaultChecked={alerts.email_enabled}
                  />
                  <span>
                    <b>Email</b>
                    <small>Deliver alert messages through an SMTP server.</small>
                  </span>
                </label>
                <div className="field-grid three">
                  <label>
                    SMTP host
                    <input name="smtp_host" defaultValue={alerts.smtp_host} />
                  </label>
                  <label>
                    Port
                    <input
                      name="smtp_port"
                      type="number"
                      min="1"
                      max="65535"
                      defaultValue={alerts.smtp_port}
                    />
                  </label>
                  <label>
                    Security
                    <select name="smtp_security" defaultValue={alerts.smtp_security}>
                      <option value="starttls">STARTTLS</option>
                      <option value="tls">TLS / SSL</option>
                      <option value="none">None</option>
                    </select>
                  </label>
                </div>
                <div className="field-grid">
                  <label>
                    SMTP username
                    <input
                      name="smtp_username"
                      placeholder={secretPlaceholder(
                        alerts.smtp_username_configured,
                        "Optional username",
                      )}
                      autoComplete="new-password"
                    />
                  </label>
                  <label>
                    SMTP password
                    <input
                      name="smtp_password"
                      type="password"
                      placeholder={secretPlaceholder(
                        alerts.smtp_password_configured,
                        "Optional password",
                      )}
                      autoComplete="new-password"
                    />
                  </label>
                </div>
                <div className="field-grid">
                  <label>
                    From address
                    <input
                      name="email_from"
                      type="email"
                      defaultValue={alerts.email_from}
                    />
                  </label>
                  <label>
                    To address
                    <input
                      name="email_to"
                      type="email"
                      defaultValue={alerts.email_to}
                    />
                  </label>
                </div>
                <TestNotificationButton
                  label="email"
                  status={actions["notification-test-email"]}
                  onTest={testNotification}
                />
              </div>
              {integrations.available_providers.map((provider) => (
                <div className="integration-provider" key={provider.key}>
                  <span>
                    <b>{provider.name}</b>
                    <small>{provider.description}</small>
                  </span>
                  <small>{provider.read_only ? "Read only" : "Installed"}</small>
                </div>
              ))}
              <ActionFeedback status={actions["integrations-save"]} />
              <div className="form-actions">
                <button
                  className="primary"
                  disabled={actions["integrations-save"]?.phase === "pending"}
                >
                  {actions["integrations-save"]?.phase === "pending" ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Save size={16} />
                  )}
                  {actions["integrations-save"]?.phase === "pending"
                    ? "Saving…"
                    : "Save integrations"}
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
              <div className="advanced-action">
                <button
                  className="secondary"
                  onClick={createBackup}
                  disabled={actions.backup?.phase === "pending"}
                >
                  {actions.backup?.phase === "pending" ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Database size={16} />
                  )}
                  {actions.backup?.phase === "pending"
                    ? "Creating backup…"
                    : "Create backup"}
                </button>
                <ActionFeedback status={actions.backup} />
              </div>
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
