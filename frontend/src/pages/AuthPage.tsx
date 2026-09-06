import { FormEvent, useState } from "react";
import { Bot, Server, ShieldCheck } from "lucide-react";
import { api } from "../api";
import { Brand } from "../App";

export default function AuthPage({
  mode,
  onAuthenticated,
}: {
  mode: "setup" | "login";
  onAuthenticated: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [credentials, setCredentials] = useState<{
    username: string;
    password: string;
  }>();
  const [mfaCode, setMfaCode] = useState("");
  const [step, setStep] = useState(0);
  const [setup, setSetup] = useState({
    server_name: "Tower",
    username: "",
    password: "",
    demo_mode: false,
  });
  const advanceSetup = () => {
    if (
      step === 1 &&
      (setup.username.length < 3 || setup.password.length < 12)
    ) {
      setError("Choose a username and a password of at least 12 characters.");
      return;
    }
    setError("");
    setStep((currentStep) => currentStep + 1);
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mode === "setup" && step < 2) {
      advanceSetup();
      return;
    }
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const loginCredentials = credentials ?? {
      username: String(form.get("username") ?? ""),
      password: String(form.get("password") ?? ""),
    };
    const payload =
      mode === "setup"
        ? setup
        : {
            ...loginCredentials,
            ...(credentials ? { code: mfaCode } : {}),
          };
    try {
      const result = await api<{ mfa_required?: boolean }>(
        mode === "setup" ? "/api/auth/setup" : "/api/auth/login",
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      );
      if (result.mfa_required) {
        setCredentials(loginCredentials);
        return;
      }
      setCredentials(undefined);
      setMfaCode("");
      onAuthenticated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to continue");
      setMfaCode("");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="auth-page">
      <div className="auth-art">
        <Brand />
        <div>
          <span className="eyebrow">OBSERVE · UNDERSTAND · ACT</span>
          <h1>
            Your server,
            <br />
            <em>finally understood.</em>
          </h1>
          <p>
            Health monitoring, storage intelligence, and a safe AI assistant
            designed for Unraid.
          </p>
        </div>
        <small>Private by design. Your telemetry stays yours.</small>
      </div>
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-icon">
          <ShieldCheck />
        </div>
        <span className="eyebrow">
          {mode === "setup"
            ? `FIRST LAUNCH · ${step + 1} OF 3`
            : "WELCOME BACK"}
        </span>
        <h2>
          {mode === "setup"
            ? [
                "Welcome to ServerSense",
                "Create administrator",
                "Choose monitoring mode",
              ][step]
            : credentials
              ? "Verify your sign-in"
              : "Sign in to ServerSense"}
        </h2>
        <p>
          {mode === "setup"
            ? [
                "A short guided setup gets monitoring ready. SENSE can be configured later.",
                "This local account protects your server telemetry and settings.",
                "ServerSense will detect Unraid automatically when live monitoring begins.",
              ][step]
            : credentials
              ? "Enter a code from your authenticator app, or use an unused recovery code."
              : "Use your local administrator account."}
        </p>
        {mode === "setup" && (
          <div className="wizard-steps">
            {[0, 1, 2].map((index) => (
              <i key={index} className={index <= step ? "active" : ""} />
            ))}
          </div>
        )}
        {mode === "setup" && step === 0 && (
          <div className="wizard-feature-list">
            <div>
              <Server />
              <span>
                <b>Detect your server</b>
                <small>Unraid and ordinary Linux are supported</small>
              </span>
            </div>
            <div>
              <ShieldCheck />
              <span>
                <b>Private and read-only</b>
                <small>No server-changing AI permissions</small>
              </span>
            </div>
            <div>
              <Bot />
              <span>
                <b>SENSE is optional</b>
                <small>Monitoring works without a model</small>
              </span>
            </div>
          </div>
        )}
        {mode === "setup" && step === 1 && (
          <>
            <label>
              Server name
              <input
                value={setup.server_name}
                onChange={(e) =>
                  setSetup({ ...setup, server_name: e.target.value })
                }
                required
              />
            </label>
            <label>
              Username
              <input
                value={setup.username}
                onChange={(e) =>
                  setSetup({ ...setup, username: e.target.value })
                }
                autoComplete="username"
                required
                minLength={3}
              />
            </label>
            <label>
              Password
              <input
                value={setup.password}
                onChange={(e) =>
                  setSetup({ ...setup, password: e.target.value })
                }
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
              />
              <small>Use at least 12 characters.</small>
            </label>
          </>
        )}
        {mode === "setup" && step === 2 && (
          <>
            <label className="check">
              <input
                type="checkbox"
                checked={setup.demo_mode}
                onChange={(e) =>
                  setSetup({ ...setup, demo_mode: e.target.checked })
                }
              />
              <span>
                <b>Start with demo data</b>
                <small>
                  Explore realistic metrics without Unraid hardware. Turn this
                  off for live collection.
                </small>
              </span>
            </label>
            <div className="permission-box">
              <Bot />
              <div>
                <b>Configure SENSE later</b>
                <p>
                  Start monitoring now, then choose Ollama or any
                  OpenAI-compatible provider in Settings → AI.
                </p>
              </div>
            </div>
          </>
        )}
        {mode === "login" && !credentials && (
          <>
            <label>
              Username
              <input
                name="username"
                autoComplete="username"
                required
                minLength={3}
              />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
              />
            </label>
          </>
        )}
        {mode === "login" && credentials && (
          <label>
            Authenticator or recovery code
            <input
              name="code"
              autoComplete="one-time-code"
              value={mfaCode}
              onChange={(event) => setMfaCode(event.target.value)}
              required
              maxLength={64}
            />
            <small>
              Authenticator codes change every 30 seconds. Each code can be used
              once.
            </small>
          </label>
        )}
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <button className="primary" disabled={busy}>
          {busy
            ? "Please wait…"
            : mode === "setup"
              ? step === 0
                ? "Begin setup"
                : step === 1
                  ? "Continue"
                  : "Finish setup"
              : credentials
                ? "Verify and sign in"
                : "Sign in"}
        </button>
        {mode === "login" && credentials && (
          <button
            type="button"
            className="wizard-back"
            disabled={busy}
            onClick={() => {
              setCredentials(undefined);
              setMfaCode("");
              setError("");
            }}
          >
            Back to sign in
          </button>
        )}
        {mode === "setup" && step > 0 && (
          <button
            type="button"
            className="wizard-back"
            onClick={() => setStep(step - 1)}
          >
            Back
          </button>
        )}
      </form>
    </div>
  );
}
