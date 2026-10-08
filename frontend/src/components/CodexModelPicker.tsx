import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api";

type Props = {
  name: string;
  value: string;
  onChange: (value: string) => void;
};

export default function CodexModelPicker({ name, value, onChange }: Props) {
  const [models, setModels] = useState<Array<{ id: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((current) => current + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      setLoading(true);
      setError("");
      try {
        const result = await api<{ models: Array<{ id: string }> }>(
          "/api/settings/ai/codex/models", { signal: controller.signal },
        );
        if (!controller.signal.aborted) setModels(result.models);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load Codex models.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [revision]);

  const savedModelMissing = value && !models.some((model) => model.id === value);
  return (
    <>
      <span className="input-action">
        <select name={name} aria-label={name === "model" ? "Model" : "Fallback model"} value={value} onChange={(event) => onChange(event.target.value)} aria-busy={loading}>
          <option value="">{loading ? "Loading Codex models..." : "Select a Codex model"}</option>
          {savedModelMissing && <option value={value}>{value} (current selection)</option>}
          {models.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}
        </select>
        <button type="button" className="secondary" onClick={refresh} disabled={loading} aria-label={name === "model" ? "Refresh Codex models" : "Refresh fallback Codex models"}>
          <RefreshCw size={14} /> {error ? "Retry" : "Refresh"}
        </button>
      </span>
      {error && <small role="alert">{error}</small>}
      {!loading && !error && models.length === 0 && <small>No Codex models were returned. Check sign-in and refresh.</small>}
      <small>The catalog loads automatically. Save your selection, then test account access.</small>
    </>
  );
}
