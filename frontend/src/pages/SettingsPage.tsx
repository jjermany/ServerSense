import { FormEvent, useEffect, useState } from "react";
import {
  Bell,
  Bot,
  CheckCircle2,
  Database,
  Download,
  LoaderCircle,
  Plus,
  Plug,
  RefreshCw,
  Save,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { api } from "../api";
import { Card, PageHeader } from "../components/UI";
import { useTimeZone } from "../timeZoneContext";
type AIConfig = {
  provider: string;
  model: string;
  endpoint: string;
  api_key_configured: boolean;
  context_window: number;
  temperature: number;
  timeout_seconds: number;
  max_tool_calls: number;
  max_output_tokens: number;
  tool_calling: "auto" | "native" | "curated_context";
  background_threshold_seconds: number;
  max_runtime_seconds: number;
  max_concurrent_jobs: number;
  max_queued_jobs: number;
  max_context_chars: number;
  max_telemetry_chars: number;
  conversation_retention_days: number;
  notify_long_running_jobs: boolean;
  browser_notifications: boolean;
  proactive_insights: boolean;
  dashboard_summaries: boolean;
};
type AlertConfig = {
  free_percent_threshold: number;
  forecast_days_threshold: number;
  temperature_c_threshold: number;
  notify_storage_low: boolean;
  notify_forecast_low: boolean;
  notify_disk_smart: boolean;
  notify_disk_temperature: boolean;
  notify_container_stopped: boolean;
  notify_sense_jobs: boolean;
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
  timezone: string;
  timezone_source: "environment" | "settings" | "default" | "invalid_environment";
  timezone_configurable: boolean;
  timezone_warning?: string | null;
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
    url: string;
    api_key_configured: boolean;
  }>;
};
const alertProviderValues = (alerts: AlertConfig) => ({
  notify_storage_low: alerts.notify_storage_low,
  notify_forecast_low: alerts.notify_forecast_low,
  notify_disk_smart: alerts.notify_disk_smart,
  notify_disk_temperature: alerts.notify_disk_temperature,
  notify_container_stopped: alerts.notify_container_stopped,
  notify_sense_jobs: alerts.notify_sense_jobs,
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
  const { setTimeZone } = useTimeZone();
  const [config, setConfig] = useState<AIConfig>();
  const [alerts, setAlerts] = useState<AlertConfig>();
  const [general, setGeneral] = useState<GeneralConfig>();
  const [integrations, setIntegrations] = useState<IntegrationsConfig>();
  const [actions, setActions] = useState<Record<string, ActionStatus>>({});
  const [models, setModels] = useState<Array<{ id: string; supports_tools?: boolean | null }>>([]);
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
    const formElement = e.currentTarget;
    const form = new FormData(formElement);
    const raw = Object.fromEntries(form);
    if (form.get("browser_notifications") === "on" && "Notification" in window && Notification.permission === "default") {
      await Notification.requestPermission();
    }
    await runAction(
      "ai-save",
      "Saving AI settings…",
      async () => {
        const updated = await api<AIConfig>("/api/settings/ai", {
          method: "PUT",
          body: JSON.stringify({
            ...raw,
            context_window: Number(raw.context_window),
            temperature: Number(raw.temperature),
            timeout_seconds: Number(raw.timeout_seconds),
            max_tool_calls: Number(raw.max_tool_calls),
            max_output_tokens: Number(raw.max_output_tokens),
            background_threshold_seconds: Number(raw.background_threshold_seconds),
            max_runtime_seconds: Number(raw.max_runtime_seconds),
            max_concurrent_jobs: Number(raw.max_concurrent_jobs),
            max_queued_jobs: Number(raw.max_queued_jobs),
            max_context_chars: Number(raw.max_context_chars),
            max_telemetry_chars: Number(raw.max_telemetry_chars),
            conversation_retention_days: Number(raw.conversation_retention_days),
            notify_long_running_jobs: form.get("notify_long_running_jobs") === "on",
            browser_notifications: form.get("browser_notifications") === "on",
            proactive_insights: form.get("proactive_insights") === "on",
            dashboard_summaries: form.get("dashboard_summaries") === "on",
          }),
        });
        setConfig(updated);
        const apiKeyInput = formElement.elements.namedItem("api_key");
        if (apiKeyInput instanceof HTMLInputElement) apiKeyInput.value = "";
        return updated;
      },
      "AI settings saved securely.",
    );
  };
  const clearApiKey = async () => {
    if (!window.confirm("Clear the saved AI API key? Other AI settings will be kept.")) return;
    await runAction(
      "ai-key-clear",
      "Clearing saved API key…",
      async () => {
        const updated = await api<AIConfig>("/api/settings/ai/api-key", {
          method: "DELETE",
        });
        setConfig(updated);
        return updated;
      },
      "Saved AI API key cleared.",
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
  const discoverModels = async () => {
    await runAction(
      "ai-models",
      "Discovering provider models…",
      async () => {
        const result = await api<{ models: Array<{ id: string; supports_tools?: boolean | null }> }>(
          "/api/settings/ai/models",
        );
        setModels(result.models);
        return result;
      },
      (result) => `Found ${result.models.length} model${result.models.length === 1 ? "" : "s"}.`,
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
            notify_storage_low: form.get("notify_storage_low") === "on",
            notify_forecast_low: form.get("notify_forecast_low") === "on",
            notify_disk_smart: form.get("notify_disk_smart") === "on",
            notify_disk_temperature:
              form.get("notify_disk_temperature") === "on",
            notify_container_stopped:
              form.get("notify_container_stopped") === "on",
            notify_sense_jobs: form.get("notify_sense_jobs") === "on",
            webhook_enabled: alerts.webhook_enabled,
          }),
        });
        setAlerts(updated);
        return updated;
      },
      "Alert thresholds saved securely.",
    );
  };
  const saveTimezone = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    await runAction(
      "timezone-save",
      "Saving timezone…",
      async () => {
        const updated = await api<GeneralConfig>("/api/settings/general", {
          method: "PUT",
          body: JSON.stringify({ timezone: form.get("timezone") }),
        });
        setGeneral(updated);
        setTimeZone(updated.timezone);
        return updated;
      },
      "Timezone saved.",
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
  const refreshIntegrations = async () => {
    setIntegrations(await api<IntegrationsConfig>("/api/integrations"));
  };
  const saveMediaIntegration = async (
    e: FormEvent<HTMLFormElement>,
    integrationId?: number,
  ) => {
    e.preventDefault();
    const formElement = e.currentTarget;
    const form = new FormData(formElement);
    const key = integrationId ? `media-save-${integrationId}` : "media-add";
    await runAction(
      key,
      integrationId ? "Saving media managerâ€¦" : "Adding media managerâ€¦",
      async () => {
        const result = await api(
          integrationId
            ? `/api/integrations/${integrationId}`
            : "/api/integrations",
          {
            method: integrationId ? "PUT" : "POST",
            body: JSON.stringify({
              provider: form.get("provider"),
              name: form.get("name"),
              url: form.get("url"),
              api_key: form.get("api_key") || null,
              enabled: form.get("enabled") === "on",
            }),
          },
        );
        await refreshIntegrations();
        if (!integrationId) formElement.reset();
        return result;
      },
      integrationId ? "Media manager saved." : "Media manager added.",
    );
  };
  const testMediaIntegration = async (integrationId: number) => {
    await runAction(
      `media-test-${integrationId}`,
      "Testing media managerâ€¦",
      () =>
        api<{ detail: string }>(`/api/integrations/${integrationId}/test`, {
          method: "POST",
        }),
      (result) => result.detail,
    );
  };
  const deleteMediaIntegration = async (integrationId: number, name: string) => {
    if (!window.confirm(`Remove ${name} and its collected activity?`)) return;
    await runAction(
      `media-delete-${integrationId}`,
      "Removing media managerâ€¦",
      async () => {
        await api(`/api/integrations/${integrationId}`, { method: "DELETE" });
        await refreshIntegrations();
      },
      "Media manager removed.",
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
    <div className="page settings-page">
      <PageHeader eyebrow="CONFIGURATION" title="Settings">
        <p className="page-header-note">Server, alerts, AI, and integrations</p>
      </PageHeader>
      <div className="settings-layout">
        <aside aria-label="Settings sections">
          <small>SETTINGS</small>
          <a href="#ai">
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
            <form className="settings-form" onSubmit={submit}>
              <section className="settings-form-section">
                <div className="settings-section-heading">
                  <div><span>01</span><h3>Model connection</h3></div>
                  <p>Select the provider, model, and credentials SENSE should use.</p>
                </div>
                <div className="field-grid">
                  <label>
                    Provider
                    <select name="provider" defaultValue={config.provider}>
                      <option value="disabled">Built-in deterministic mode</option>
                      <option value="ollama">Ollama-compatible</option>
                      <option value="openai_compatible">OpenAI-compatible API</option>
                    </select>
                  </label>
                  <label>
                    Model
                    <span className="input-action">
                      <input name="model" defaultValue={config.model} list="ai-model-list" placeholder="e.g. llama3.2:3b" />
                      <button type="button" className="secondary" onClick={() => void discoverModels()} disabled={actions["ai-models"]?.phase === "pending"}>
                        <RefreshCw size={14} /> Refresh
                      </button>
                    </span>
                    <datalist id="ai-model-list">{models.map((model) => <option key={model.id} value={model.id} />)}</datalist>
                  </label>
                </div>
                <div className="field-grid">
                  <label>
                    Endpoint
                    <input name="endpoint" type="url" defaultValue={config.endpoint} placeholder="http://host.docker.internal:11434" />
                    <small>ServerSense adds /v1/chat/completions when sending requests.</small>
                  </label>
                  <div className="credential-field">
                    <label>
                      API key
                      <input name="api_key" type="password" placeholder={config.api_key_configured ? "Configured — leave blank to keep" : "Optional for local endpoints"} autoComplete="new-password" />
                    </label>
                    {config.api_key_configured && (
                      <button type="button" className="secondary" onClick={() => void clearApiKey()} disabled={actions["ai-key-clear"]?.phase === "pending"}>
                        {actions["ai-key-clear"]?.phase === "pending" ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}
                        {actions["ai-key-clear"]?.phase === "pending" ? "Clearing…" : "Clear saved key"}
                      </button>
                    )}
                  </div>
                </div>
                <ActionFeedback status={actions["ai-models"]} />
              </section>

              <section className="settings-form-section">
                <div className="settings-section-heading">
                  <div><span>02</span><h3>Generation behavior</h3></div>
                  <p>Control response size, model behavior, and provider timeouts.</p>
                </div>
                <div className="settings-control-grid">
                  <label>Context window<input name="context_window" type="number" min="1024" defaultValue={config.context_window} /></label>
                  <label>Temperature<input name="temperature" type="number" min="0" max="2" step="0.1" defaultValue={config.temperature} /></label>
                  <label>Maximum tool calls<input name="max_tool_calls" type="number" min="1" max="12" defaultValue={config.max_tool_calls} /></label>
                  <label>Provider timeout (seconds)<input name="timeout_seconds" type="number" min="5" max="600" defaultValue={config.timeout_seconds} /></label>
                  <label>Maximum response tokens<input name="max_output_tokens" type="number" min="64" max="4096" defaultValue={config.max_output_tokens} /></label>
                  <label>Tool compatibility<select name="tool_calling" defaultValue={config.tool_calling}><option value="auto">Auto fallback</option><option value="native">Require native tools</option><option value="curated_context">Curated context only</option></select></label>
                </div>
              </section>

              <section className="settings-form-section">
                <div className="settings-section-heading">
                  <div><span>03</span><h3>Jobs, context, and retention</h3></div>
                  <p>Set hard resource boundaries for interactive model work.</p>
                </div>
                <div className="settings-control-grid">
                  <label>Background after (seconds)<input name="background_threshold_seconds" type="number" min="5" max="600" defaultValue={config.background_threshold_seconds} /><small>Changes presentation only; processing continues.</small></label>
                  <label>Maximum runtime (seconds)<input name="max_runtime_seconds" type="number" min="30" max="3600" defaultValue={config.max_runtime_seconds} /><small>Hard wall-clock limit for one inference.</small></label>
                  <label>Concurrent AI jobs<input name="max_concurrent_jobs" type="number" min="1" max="4" defaultValue={config.max_concurrent_jobs} /></label>
                  <label>Maximum queued jobs<input name="max_queued_jobs" type="number" min="1" max="100" defaultValue={config.max_queued_jobs} /></label>
                  <label>Conversation retention (days)<input name="conversation_retention_days" type="number" min="1" max="365" defaultValue={config.conversation_retention_days} /></label>
                  <label>Maximum AI context (characters)<input name="max_context_chars" type="number" min="12000" max="200000" step="1000" defaultValue={config.max_context_chars} /></label>
                  <label>Maximum telemetry (characters)<input name="max_telemetry_chars" type="number" min="2000" max="100000" step="1000" defaultValue={config.max_telemetry_chars} /></label>
                </div>
              </section>

              <section className="settings-form-section">
                <div className="settings-section-heading">
                  <div><span>04</span><h3>Automation and notifications</h3></div>
                  <p>These optional features run separately from deterministic monitoring.</p>
                </div>
                <div className="settings-toggle-grid">
                  <label className="check"><input name="notify_long_running_jobs" type="checkbox" defaultChecked={config.notify_long_running_jobs} /><span><b>Long-running job notifications</b><small>Use notifications by default when a long SENSE request finishes.</small></span></label>
                  <label className="check"><input name="browser_notifications" type="checkbox" defaultChecked={config.browser_notifications} /><span><b>Browser notifications</b><small>Allow eligible completed jobs to show a system notification.</small></span></label>
                  <label className="check"><input name="proactive_insights" type="checkbox" defaultChecked={config.proactive_insights} /><span><b>Explain new alerts with SENSE</b><small>Request a model explanation after deterministic alerts are safely recorded.</small></span></label>
                  <label className="check"><input name="dashboard_summaries" type="checkbox" defaultChecked={config.dashboard_summaries} /><span><b>Add a cached AI dashboard summary</b><small>First attempt occurs within about five minutes. It needs a configured model and one storage sample; a forecast may still be learning.</small></span></label>
                </div>
                <div className="summary-timing-note">
                  <RefreshCw size={16} />
                  <p><b>Summary timing</b><span>Refreshes every six hours, or after new alert/media activity once the prior summary is at least 15 minutes old. Cached summaries are shown for up to 12 hours.</span></p>
                </div>
              </section>
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
                <ActionFeedback status={actions["ai-key-clear"]} />
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
                  Choose when alerts are created and which ones are sent to your
                  notification providers.
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
                  Notify before projected exhaustion (days)
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
              <div className="notification-preferences">
                <p>
                  <b>Notification categories</b>
                  <br />
                  Monitoring alerts are always recorded. Uncheck a category to keep
                  it out of webhook, Discord, Pushover, and email delivery.
                </p>
                <div className="field-grid">
                  <label className="check">
                    <input
                      name="notify_storage_low"
                      type="checkbox"
                      defaultChecked={alerts.notify_storage_low}
                    />
                    <span>Low free storage</span>
                  </label>
                  <label className="check">
                    <input
                      name="notify_forecast_low"
                      type="checkbox"
                      defaultChecked={alerts.notify_forecast_low}
                    />
                    <span>Projected storage exhaustion</span>
                  </label>
                  <label className="check">
                    <input
                      name="notify_disk_smart"
                      type="checkbox"
                      defaultChecked={alerts.notify_disk_smart}
                    />
                    <span>SMART warnings and failures</span>
                  </label>
                  <label className="check">
                    <input
                      name="notify_disk_temperature"
                      type="checkbox"
                      defaultChecked={alerts.notify_disk_temperature}
                    />
                    <span>High disk temperature</span>
                  </label>
                  <label className="check">
                    <input
                      name="notify_container_stopped"
                      type="checkbox"
                      defaultChecked={alerts.notify_container_stopped}
                    />
                    <span>Containers stopped over 10 minutes</span>
                  </label>
                  <label className="check">
                    <input
                      name="notify_sense_jobs"
                      type="checkbox"
                      defaultChecked={alerts.notify_sense_jobs}
                    />
                    <span>Long-running SENSE job results</span>
                  </label>
                </div>
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
                    : "Save alert preferences"}
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
              <div>
                <small>TIMEZONE</small>
                <b>{general.timezone}</b>
              </div>
            </div>
            {general.timezone_warning && (
              <div className="form-error">{general.timezone_warning}</div>
            )}
            {general.timezone_configurable ? (
              <form onSubmit={saveTimezone}>
                <label>
                  Display timezone
                  <input
                    name="timezone"
                    defaultValue={general.timezone}
                    placeholder="America/Chicago"
                    required
                  />
                  <small>
                    Use an IANA timezone name. This fallback is available because the
                    container TZ variable is not set.
                  </small>
                </label>
                <ActionFeedback status={actions["timezone-save"]} />
                <div className="form-actions">
                  <button
                    className="primary"
                    disabled={actions["timezone-save"]?.phase === "pending"}
                  >
                    <Save size={16} />
                    Save timezone
                  </button>
                </div>
              </form>
            ) : (
              <p className="settings-note">
                Display timezone is controlled by the container <code>TZ</code> variable.
              </p>
            )}
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
            <div className="media-integrations">
              <div className="settings-title compact">
                <span>
                  <Bot />
                </span>
                <div>
                  <h3>AI MEDIA CONTEXT</h3>
                  <p>
                    Add any number of named Sonarr or Radarr instances. SENSE uses
                    their normalized, read-only history only while AI is enabled.
                  </p>
                </div>
              </div>
              {integrations.configured.map((item) => (
                <form
                  className="media-integration-editor"
                  key={item.id}
                  onSubmit={(event) => void saveMediaIntegration(event, item.id)}
                >
                  <div className="field-grid three">
                    <label>
                      Type
                      <input type="hidden" name="provider" value={item.provider} />
                      <select value={item.provider} disabled>
                        <option value="sonarr">Sonarr</option>
                        <option value="radarr">Radarr</option>
                      </select>
                    </label>
                    <label>
                      Instance name
                      <input name="name" defaultValue={item.name} required />
                    </label>
                    <label>
                      URL
                      <input name="url" type="url" defaultValue={item.url} required />
                    </label>
                  </div>
                  <div className="field-grid">
                    <label>
                      API key
                      <input
                        name="api_key"
                        type="password"
                        autoComplete="new-password"
                        placeholder={secretPlaceholder(
                          item.api_key_configured,
                          "Sonarr or Radarr API key",
                        )}
                      />
                    </label>
                    <label className="check media-enabled">
                      <input name="enabled" type="checkbox" defaultChecked={item.enabled} />
                      <span>Collect AI context</span>
                    </label>
                  </div>
                  <ActionFeedback status={actions[`media-save-${item.id}`]} />
                  <ActionFeedback status={actions[`media-test-${item.id}`]} />
                  <ActionFeedback status={actions[`media-delete-${item.id}`]} />
                  <div className="form-actions media-actions">
                    <button
                      className="primary"
                      disabled={actions[`media-save-${item.id}`]?.phase === "pending"}
                    >
                      {actions[`media-save-${item.id}`]?.phase === "pending" ? (
                        <LoaderCircle className="spin" size={16} />
                      ) : (
                        <Save size={16} />
                      )}
                      Save {item.name}
                    </button>
                    <button
                      className="secondary"
                      type="button"
                      onClick={() => void testMediaIntegration(item.id)}
                      disabled={actions[`media-test-${item.id}`]?.phase === "pending"}
                    >
                      Test connection
                    </button>
                    <button
                      className="secondary danger"
                      type="button"
                      onClick={() => void deleteMediaIntegration(item.id, item.name)}
                      disabled={actions[`media-delete-${item.id}`]?.phase === "pending"}
                    >
                      <Trash2 size={16} /> Remove
                    </button>
                  </div>
                </form>
              ))}
              <form
                className="media-integration-editor add"
                onSubmit={(event) => void saveMediaIntegration(event)}
              >
                <h4>ADD MEDIA MANAGER</h4>
                <div className="field-grid three">
                  <label>
                    Type
                    <select name="provider" defaultValue="radarr">
                      <option value="sonarr">Sonarr</option>
                      <option value="radarr">Radarr</option>
                    </select>
                  </label>
                  <label>
                    Instance name
                    <input name="name" placeholder="Movies or Anime" required />
                  </label>
                  <label>
                    URL
                    <input name="url" type="url" placeholder="http://radarr:7878" required />
                  </label>
                </div>
                <div className="field-grid">
                  <label>
                    API key
                    <input name="api_key" type="password" required autoComplete="new-password" />
                  </label>
                  <label className="check media-enabled">
                    <input name="enabled" type="checkbox" defaultChecked />
                    <span>Collect AI context</span>
                  </label>
                </div>
                <ActionFeedback status={actions["media-add"]} />
                <div className="form-actions">
                  <button
                    className="primary"
                    disabled={actions["media-add"]?.phase === "pending"}
                  >
                    {actions["media-add"]?.phase === "pending" ? (
                      <LoaderCircle className="spin" size={16} />
                    ) : (
                      <Plus size={16} />
                    )}
                    Add instance
                  </button>
                </div>
              </form>
            </div>
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
