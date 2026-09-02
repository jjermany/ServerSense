import { AlertTriangle, Check, Info, X } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Alert } from "../types";
import { Card, Empty, PageHeader } from "../components/UI";
import { formatDateTime } from "../timeFormat";
import { useTimeZone } from "../timeZoneContext";
import { useLiveQuery } from "../hooks/useLiveQuery";
export default function AlertsPage() {
  const { timeZone } = useTimeZone();
  const { data: rows = [], error, refresh, setData } =
    useLiveQuery<Alert[]>("/api/alerts");
  const ack = async (id: number) => {
    await api(`/api/alerts/${id}/acknowledge`, { method: "POST" });
    refresh();
  };
  const dismiss = async (id: number) => {
    await api(`/api/alerts/${id}/dismiss`, { method: "POST" });
    setData((current) => current?.filter((row) => row.id !== id));
  };
  return (
    <div className="page">
      <PageHeader eyebrow="EVENTS & NOTIFICATIONS" title="Alerts">
        <Link className="secondary link-button" to="/settings#alerts">
          Alert settings
        </Link>
      </PageHeader>
      {error && <div className="form-error">{error}</div>}
      <Card className="alerts-page">
        {rows.length ? (
          rows.map((row) => (
            <article key={row.id} className={!row.active ? "resolved" : ""}>
              <span className={`alert-icon ${row.severity}`}>
                {row.severity === "info" ? <Info /> : <AlertTriangle />}
              </span>
              <div>
                <span className="eyebrow">{row.type.replaceAll("_", " ")}</span>
                <h3>{row.title}</h3>
                <p>{row.message}</p>
                <small>{formatDateTime(row.created_at, timeZone)}</small>
              </div>
              <div className="alert-actions">
                {row.active && !row.acknowledged_at && (
                  <button className="secondary" onClick={() => ack(row.id)}>
                    <Check size={15} />
                    Acknowledge
                  </button>
                )}
                {row.acknowledged_at && (
                  <small>Acknowledged {formatDateTime(row.acknowledged_at, timeZone)}</small>
                )}
                <button className="secondary" onClick={() => dismiss(row.id)}>
                  <X size={15} />
                  Dismiss
                </button>
              </div>
            </article>
          ))
        ) : (
          <Empty>No alerts have been recorded.</Empty>
        )}
      </Card>
    </div>
  );
}
