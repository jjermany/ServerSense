import { useEffect, useState } from "react";
import { AlertTriangle, Check, Info } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Alert } from "../types";
import { Card, Empty, PageHeader } from "../components/UI";
export default function AlertsPage() {
  const [rows, setRows] = useState<Alert[]>([]);
  const load = () => api<Alert[]>("/api/alerts").then(setRows);
  useEffect(() => {
    void load();
  }, []);
  const ack = async (id: number) => {
    await api(`/api/alerts/${id}/acknowledge`, { method: "POST" });
    void load();
  };
  return (
    <div className="page">
      <PageHeader eyebrow="EVENTS & NOTIFICATIONS" title="Alerts">
        <Link className="secondary link-button" to="/settings#alerts">
          Alert settings
        </Link>
      </PageHeader>
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
                <small>{new Date(row.created_at).toLocaleString()}</small>
              </div>
              {row.active && (
                <button className="secondary" onClick={() => ack(row.id)}>
                  <Check size={15} />
                  Acknowledge
                </button>
              )}
            </article>
          ))
        ) : (
          <Empty>No alerts have been recorded.</Empty>
        )}
      </Card>
    </div>
  );
}
