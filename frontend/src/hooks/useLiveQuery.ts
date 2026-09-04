import { useCallback, useEffect, useState, type SetStateAction } from "react";
import { api } from "../api";

export const LIVE_REFRESH_INTERVAL_MS = 5_000;
export const DOCKER_REFRESH_INTERVAL_MS = 15_000;
export const SLOW_REFRESH_INTERVAL_MS = 60_000;

const MAX_CACHE_ENTRIES = 50;
const liveDataCache = new Map<string, unknown>();

export function clearLiveQueryCache() {
  liveDataCache.clear();
}

function cacheLiveData<T>(path: string, data: T) {
  liveDataCache.delete(path);
  liveDataCache.set(path, data);
  if (liveDataCache.size > MAX_CACHE_ENTRIES) {
    const oldest = liveDataCache.keys().next().value;
    if (oldest !== undefined) liveDataCache.delete(oldest);
  }
}

export function useLiveQuery<T>(
  path: string,
  intervalMs = LIVE_REFRESH_INTERVAL_MS,
) {
  const [data, setData] = useState<T | undefined>(
    () => liveDataCache.get(path) as T | undefined,
  );
  const [error, setError] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const refresh = useCallback(() => setRefreshVersion((value) => value + 1), []);
  const updateData = useCallback(
    (update: SetStateAction<T | undefined>) => {
      setData((current) => {
        const next =
          typeof update === "function"
            ? (update as (value: T | undefined) => T | undefined)(current)
            : update;
        if (next === undefined) liveDataCache.delete(path);
        else cacheLiveData(path, next);
        return next;
      });
    },
    [path],
  );

  useEffect(() => {
    let active = true;
    let hasData = liveDataCache.has(path);
    let refreshing = false;

    const refresh = async () => {
      if (refreshing || document.visibilityState === "hidden") return;
      refreshing = true;
      try {
        const next = await api<T>(path);
        if (active) {
          hasData = true;
          cacheLiveData(path, next);
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
    const timer = window.setInterval(() => void refresh(), intervalMs);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [intervalMs, path, refreshVersion]);

  return { data, error, refresh, setData: updateData };
}
