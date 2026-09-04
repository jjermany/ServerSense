import { Component, type ReactNode } from "react";
import { Brand } from "../App";

const RETRY_PARAM = "_ssretry";

function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /dynamically imported module|failed to fetch|importing a module script failed|loading chunk|chunkloaderror/i.test(
    message,
  );
}

function reloadForFreshBuild(): void {
  const url = new URL(window.location.href);
  url.searchParams.set(RETRY_PARAM, String(Date.now()));
  window.location.assign(url.toString());
}

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * A stale tab can hold references to JS chunk files that no longer exist
 * once ServerSense has been rebuilt/redeployed (Vite hashes chunk names per
 * build). Navigating into a lazily-loaded route then throws a module-load
 * error that would otherwise leave the page area permanently blank inside
 * <Suspense>, even though the app shell around it still renders fine.
 *
 * We reload once automatically to pick up the new build; if that still
 * fails (a real error, not a stale build) we fall back to asking the user
 * to reload rather than looping forever.
 */
export default class ChunkErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  alreadyRetried(): boolean {
    try {
      return new URLSearchParams(window.location.search).has(RETRY_PARAM);
    } catch {
      /* URL parsing unavailable; treat as already retried and skip the loop */
      return true;
    }
  }

  componentDidCatch(error: Error): void {
    if (isChunkLoadError(error) && !this.alreadyRetried()) reloadForFreshBuild();
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const stale = isChunkLoadError(error);
    const message =
      stale && !this.alreadyRetried()
        ? "A newer version of ServerSense is available. Reloading…"
        : stale
          ? "This page still couldn't load after reloading. If ServerSense was just updated, give the container a moment and try again."
          : "This page failed to load.";
    return (
      <div className="splash">
        <Brand />
        <span className="pulse">{message}</span>
        <button
          className="connection-retry"
          type="button"
          onClick={() => window.location.reload()}
        >
          Reload now
        </button>
      </div>
    );
  }
}
