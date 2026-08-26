import { useEffect, useState } from "react";
import { api } from "../api";

export const LIVE_REFRESH_INTERVAL_MS = 5_000;

export function useLiveQuery<T>(path: string) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let hasData = false;
    let refreshing = false;

    const refresh = async () => {
      if (refreshing || document.visibilityState === "hidden") return;
      refreshing = true;
      try {
        const next = await api<T>(path);
        if (active) {
          hasData = true;
          setData(next);
          setError("");
        }
      } catch (reason) {
        if (active && !hasData) {
          setError(reason instanceof Error ? reason.message : "Unable to load live data");
        }
      } finally {
        refreshing = false;
      }
    };

    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };

    void refresh();
    const timer = window.setInterval(() => void refresh(), LIVE_REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [path]);

  return { data, error };
}
