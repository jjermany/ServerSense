import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api";
import ChunkErrorBoundary from "./components/ChunkErrorBoundary";
import Layout from "./components/Layout";
import AuthPage from "./pages/AuthPage";
import { TimeZoneProvider } from "./timeZone";

const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const StoragePage = lazy(() => import("./pages/StoragePage"));
const DisksPage = lazy(() => import("./pages/DisksPage"));
const DiskDetailsPage = lazy(() => import("./pages/DiskDetailsPage"));
const DockerPage = lazy(() => import("./pages/DockerPage"));
const AlertsPage = lazy(() => import("./pages/AlertsPage"));
const SensePage = lazy(() => import("./pages/SensePage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

type AuthState = "loading" | "setup" | "login" | "authenticated";
export const AUTH_RETRY_DELAY_MS = 2_000;

export default function App() {
  const [auth, setAuth] = useState<AuthState>("loading");
  const [connectionDelayed, setConnectionDelayed] = useState(false);
  const [retryVersion, setRetryVersion] = useState(0);
  useEffect(() => {
    // Once the freshly-reloaded build has had time to load its chunks
    // without a repeat failure, drop the retry marker so a genuinely new
    // stale-build error later can trigger another automatic reload.
    const url = new URL(window.location.href);
    if (!url.searchParams.has("_ssretry")) return;
    const timer = window.setTimeout(() => {
      url.searchParams.delete("_ssretry");
      window.history.replaceState(null, "", url.toString());
    }, 4_000);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;
    const controller = new AbortController();

    const checkAuthentication = async () => {
      try {
        const status = await api<{ setup_required: boolean }>(
          "/api/auth/status",
          { signal: controller.signal },
        );
        if (!active) return;
        if (status.setup_required) {
          setAuth("setup");
          setConnectionDelayed(false);
          return;
        }
        try {
          await api("/api/auth/me", { signal: controller.signal });
          if (active) {
            setAuth("authenticated");
            setConnectionDelayed(false);
          }
        } catch (reason) {
          if (reason instanceof ApiError && reason.status === 401) {
            if (active) setAuth("login");
            return;
          }
          throw reason;
        }
      } catch {
        if (!active) return;
        setConnectionDelayed(true);
        retryTimer = window.setTimeout(
          () => void checkAuthentication(),
          AUTH_RETRY_DELAY_MS,
        );
      }
    };

    void checkAuthentication();
    return () => {
      active = false;
      controller.abort();
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [retryVersion]);
  if (auth === "loading")
    return (
      <div className="splash">
        <Brand />
        <span className="pulse">
          {connectionDelayed
            ? "Server is taking longer than expected. Retrying…"
            : "Connecting to your server…"}
        </span>
        {connectionDelayed && (
          <button
            className="connection-retry"
            type="button"
            onClick={() => {
              setConnectionDelayed(false);
              setRetryVersion((version) => version + 1);
            }}
          >
            Retry now
          </button>
        )}
      </div>
    );
  if (auth !== "authenticated")
    return (
      <AuthPage mode={auth} onAuthenticated={() => setAuth("authenticated")} />
    );
  return (
    <TimeZoneProvider>
    <ChunkErrorBoundary>
    <Suspense
      fallback={
        <div className="splash">
          <span className="pulse">Loading ServerSense…</span>
        </div>
      }
    >
      <Routes>
        <Route element={<Layout onLogout={() => setAuth("login")} />}>
          <Route index element={<DashboardPage />} />
          <Route path="storage" element={<StoragePage />} />
          <Route path="disks" element={<DisksPage />} />
          <Route path="disks/:diskId" element={<DiskDetailsPage />} />
          <Route path="docker" element={<DockerPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="sense" element={<SensePage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Suspense>
    </ChunkErrorBoundary>
    </TimeZoneProvider>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand">
      <span className="brand-mark">
        <i />
        <i />
        <i />
      </span>
      {!compact && (
        <span>
          Server<b>Sense</b>
        </span>
      )}
    </div>
  );
}
