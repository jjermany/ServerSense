import { FormEvent, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api } from "../api";
import { Card } from "./UI";

type MFAStatus = { enabled: boolean; recovery_codes_remaining: number };
type Enrollment = {
  secret: string;
  qr_code: string;
  expires_in_seconds: number;
};

export default function SecuritySettings() {
  const [status, setStatus] = useState<MFAStatus>();
  const [enrollment, setEnrollment] = useState<Enrollment>();
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [action, setAction] = useState<"disable" | "recovery-codes" | null>(
    null,
  );
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const loadStatus = () => {
    void api<MFAStatus>("/api/auth/mfa")
      .then((result) => {
        setError("");
        setStatus(result);
      })
      .catch((reason: unknown) => {
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load MFA status",
        );
      });
  };
  useEffect(loadStatus, []);

  const cancel = () => {
    setEnrollment(undefined);
    setAction(null);
    setPassword("");
    setCode("");
    setError("");
    setMessage("");
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    const operation = action ?? (enrollment ? "confirm" : "setup");
    try {
      const payload = { password, ...(operation !== "setup" ? { code } : {}) };
      if (operation === "setup") {
        const result = await api<Enrollment>("/api/auth/mfa/setup", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setEnrollment(result);
      } else {
        const result = await api<{
          enabled: boolean;
          recovery_codes?: string[];
        }>(`/api/auth/mfa/${operation}`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setStatus({
          enabled: result.enabled,
          recovery_codes_remaining: result.recovery_codes?.length ?? 0,
        });
        setRecoveryCodes(result.recovery_codes ?? []);
        cancel();
        setMessage(
          result.enabled
            ? "MFA is enabled. Other sessions have been signed out."
            : "MFA is disabled. Other sessions have been signed out.",
        );
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to update MFA",
      );
      setCode("");
    } finally {
      setBusy(false);
    }
  };
  const downloadCodes = () => {
    const blob = new Blob(
      [
        "ServerSense MFA recovery codes\nKeep these somewhere private. Each code can be used once, with your password.\n\n",
        recoveryCodes.join("\n"),
        "\n",
      ],
      { type: "text/plain" },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "serversense-recovery-codes.txt";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <Card className="settings-card">
      <span id="security" />
      <div className="settings-title">
        <span>
          <ShieldCheck />
        </span>
        <div>
          <h2>Account security</h2>
          <p>
            Optional multi-factor authentication (MFA) for your administrator
            account.
          </p>
        </div>
      </div>
      {!status && !error && <p role="status">Loading security settings...</p>}
      {status && (
        <p className="mfa-status">
          Authenticator MFA:{" "}
          <strong>{status.enabled ? "Enabled" : "Off"}</strong>
        </p>
      )}
      {message && (
        <p className="action-feedback success" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {!status && error && (
        <button type="button" className="secondary" onClick={loadStatus}>
          Retry security settings
        </button>
      )}
      {recoveryCodes.length > 0 ? (
        <section className="mfa-recovery" aria-label="Recovery codes">
          <h3>Save your recovery codes</h3>
          <p>
            These codes are shown only now. Store them in a password manager or
            another safe place. Each code works once with your password if you
            lose your authenticator.
          </p>
          <ul>
            {recoveryCodes.map((value) => (
              <li key={value}>
                <code>{value}</code>
              </li>
            ))}
          </ul>
          <div className="mfa-actions">
            <button type="button" className="secondary" onClick={downloadCodes}>
              Download recovery codes
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => setRecoveryCodes([])}
            >
              I saved my recovery codes
            </button>
          </div>
        </section>
      ) : status && (!status.enabled || action) ? (
        <form className="settings-form" onSubmit={submit}>
          {!status.enabled && !enrollment && (
            <p>
              Add a code from an authenticator app when signing in. MFA stays
              off until you finish setup.
            </p>
          )}
          {action === "disable" && (
            <p>
              Disabling MFA removes the second step from login. Confirm with
              your password and a current authenticator or unused recovery code.
            </p>
          )}
          {action === "recovery-codes" && (
            <p>
              This replaces all existing recovery codes. Confirm with your
              password and a current authenticator or unused recovery code.
            </p>
          )}
          <label>
            Current password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              maxLength={256}
              required
              disabled={busy}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {enrollment && (
            <div className="mfa-enrollment">
              <img
                src={enrollment.qr_code}
                alt="Scan this QR code with your authenticator app"
                className="mfa-qr"
              />
              <div>
                <h3>Scan with your authenticator</h3>
                <p>
                  Use an app such as Google Authenticator, Microsoft
                  Authenticator, or 1Password. Setup expires after 10 minutes.
                </p>
                <label>
                  Manual setup key
                  <input
                    readOnly
                    value={enrollment.secret}
                    spellCheck={false}
                    autoComplete="off"
                  />
                </label>
                <p>
                  For manual setup, choose a time-based code with 6 digits and a
                  30-second interval.
                </p>
              </div>
            </div>
          )}
          {(enrollment || action) && (
            <label>
              {enrollment
                ? "Authenticator code"
                : "Authenticator or recovery code"}
              <input
                autoComplete="one-time-code"
                inputMode={enrollment ? "numeric" : "text"}
                value={code}
                maxLength={enrollment ? 6 : 64}
                pattern={enrollment ? "[0-9]{6}" : undefined}
                required
                disabled={busy}
                onChange={(event) => setCode(event.target.value)}
              />
            </label>
          )}
          <div className="mfa-actions">
            <button className="primary" disabled={busy}>
              {busy
                ? "Please wait..."
                : action === "disable"
                  ? "Confirm disable MFA"
                  : action === "recovery-codes"
                    ? "Generate new recovery codes"
                    : enrollment
                      ? "Verify and enable MFA"
                      : "Set up MFA"}
            </button>
            {(enrollment || action) && (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={cancel}
              >
                Cancel
              </button>
            )}
          </div>
        </form>
      ) : status?.enabled ? (
        <div className="mfa-management">
          <p>
            You have {status.recovery_codes_remaining} recovery codes remaining.
            MFA is required for both local and remote logins.
          </p>
          <div className="mfa-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => {
                cancel();
                setAction("recovery-codes");
              }}
            >
              Replace recovery codes
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                cancel();
                setAction("disable");
              }}
            >
              Disable MFA
            </button>
          </div>
        </div>
      ) : null}
    </Card>
  );
}
