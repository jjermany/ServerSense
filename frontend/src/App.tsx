import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import Layout from "./components/Layout";
import AuthPage from "./pages/AuthPage";

const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const StoragePage = lazy(() => import("./pages/StoragePage"));
const DisksPage = lazy(() => import("./pages/DisksPage"));
const DiskDetailsPage = lazy(() => import("./pages/DiskDetailsPage"));
const DockerPage = lazy(() => import("./pages/DockerPage"));
const AlertsPage = lazy(() => import("./pages/AlertsPage"));
const SensePage = lazy(() => import("./pages/SensePage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

type AuthState = "loading" | "setup" | "login" | "authenticated";

export default function App() {
  const [auth, setAuth] = useState<AuthState>("loading");
  useEffect(() => {
    api<{ setup_required: boolean }>("/api/auth/status")
      .then((status) =>
        status.setup_required
          ? setAuth("setup")
          : api("/api/auth/me")
              .then(() => setAuth("authenticated"))
              .catch(() => setAuth("login")),
      )
      .catch(() => setAuth("login"));
  }, []);
  if (auth === "loading")
    return (
      <div className="splash">
        <Brand />
        <span className="pulse">Connecting to your server…</span>
      </div>
    );
  if (auth !== "authenticated")
    return (
      <AuthPage mode={auth} onAuthenticated={() => setAuth("authenticated")} />
    );
  return (
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
