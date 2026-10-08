import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { formatDateTime } from "../timeFormat";
import { useTimeZone } from "../timeZoneContext";

type Login = {
  state: "pending" | "signed_in" | "failed" | "cancelled" | "expired";
  verification_url?: string;
  user_code?: string;
};
type Account = {
  signed_in: boolean;
  plan: string | null;
  login: Login | null;
  measured_at: string;
  limits: Array<{
    bucket: string;
    window: string;
    used_percent: number;
    window_minutes: number | null;
    resets_at: string | null;
    exhausted: boolean;
  }>;
};

export default function CodexSettings() {
  const { timeZone } = useTimeZone();
  const [account, setAccount] = useState<Account>();
  const [login, setLogin] = useState<Login | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const refreshing = useRef(false);
  const refresh = useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    const current = generation.current;
    try {
      const result = await api<Account>("/api/settings/ai/codex/account");
      if (current !== generation.current) return;
      setAccount(result);
      setLogin(result.login);
      setError("");
    } catch (cause) {
      if (current === generation.current) {
        setError(cause instanceof Error ? cause.message : "Could not load the Codex account.");
      }
    } finally {
      refreshing.current = false;
    }
  }, []);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => { if (active) void refresh(); });
    return () => { active = false; generation.current += 1; };
  }, [refresh]);

  useEffect(() => {
    if (login?.state !== "pending" && !(login?.state === "signed_in" && !account?.signed_in)) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 3000);
    const visible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [login?.state, account?.signed_in, refresh]);

  const action = async (kind: "login" | "cancel" | "logout" | "refresh") => {
    generation.current += 1;
    setBusy(true);
    setError("");
    try {
      if (kind === "login") {
        setLogin(await api<Login>("/api/settings/ai/codex/login", { method: "POST" }));
      } else {
        if (kind !== "refresh") {
          await api(`/api/settings/ai/codex/${kind === "cancel" ? "login" : "account"}`, { method: "DELETE" });
          setLogin(null);
          if (kind === "logout") setAccount(undefined);
        }
        await refresh();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Codex account action failed. Please retry.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="codex-settings">
      <p>
        Use your ChatGPT plan with Codex. Sign-in persists on this server across restarts.
        Your subscription allowance also covers optional model summaries and alert explanations.
      </p>
      <p role="status">
        {account?.signed_in ? `ChatGPT connected${account.plan ? ` (${account.plan})` : ""}` : "ChatGPT is not connected."}
        {busy ? " Updating account…" : ""}
      </p>
      <div className="settings-actions">
        {account?.signed_in ? (
          <button type="button" className="secondary" disabled={busy} onClick={() => void action("logout")}>Sign out of ChatGPT</button>
        ) : (
          <button type="button" disabled={busy || login?.state === "pending"} onClick={() => void action("login")}>Sign in with ChatGPT</button>
        )}
        <button type="button" className="secondary" disabled={busy} onClick={() => void action("refresh")}>Refresh account and usage</button>
      </div>
      {login?.state === "pending" && (
        <div className="codex-device-login">
          <p>Open <a href={login.verification_url} target="_blank" rel="noopener noreferrer">ChatGPT device sign-in</a> and enter this one-time code:</p>
          <strong>{login.user_code}</strong>
          <p>Keep this code private. Enable device-code login in ChatGPT security settings or workspace permissions if needed. Waiting for sign-in; the challenge expires after 15 minutes.</p>
          <button type="button" className="secondary" disabled={busy} onClick={() => void action("cancel")}>Cancel sign-in</button>
        </div>
      )}
      {(login?.state === "failed" || login?.state === "expired") && <p role="alert">Device sign-in {login.state}. Start a new sign-in to retry.</p>}
      {error && <p role="alert">{error}</p>}
      {account?.signed_in && (
        <div aria-label="Codex subscription usage">
          {account.limits.length ? account.limits.map((limit, index) => (
            <p key={`${limit.bucket}-${limit.window}-${index}`}>
              {limit.bucket} {limit.window_minutes ? `(${limit.window_minutes >= 1440 ? `${Math.round(limit.window_minutes / 1440)} days` : `${limit.window_minutes} minutes`})` : limit.window}: {limit.used_percent}% used.
              {limit.exhausted && " This allowance window is exhausted."}
              {limit.resets_at ? ` Resets ${formatDateTime(limit.resets_at, timeZone)}.` : " Reset time has not been reported."}
            </p>
          )) : <p>OpenAI has not supplied usage or reset details. Model access is verified by Test connection.</p>}
          <small>Usage checked {formatDateTime(account.measured_at, timeZone)}. Other Codex sessions may also use this allowance.</small>
        </div>
      )}
      <small>Save the provider choice before refreshing models. For SENSE tool calls, use a Context window of at least 32768. Model discovery shows a catalog; test the selected model to verify account access. Temperature is managed by Codex, and the response setting bounds visible output conservatively.</small>
    </div>
  );
}
