import { Box } from "lucide-react";
import { formatBytes } from "../api";
import type { Container } from "../types";
import { Card, PageHeader, Status } from "../components/UI";
import { useLiveQuery } from "../hooks/useLiveQuery";
const formatUptime = (seconds: number | null) => {
  if (seconds == null) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days ? `${days}d ${hours}h` : `${hours}h`;
};
export default function DockerPage() {
  const { data: rows = [], error } = useLiveQuery<Container[]>("/api/docker");
  return (
    <div className="page">
      <PageHeader eyebrow="CONTAINER MONITORING" title="Docker">
        <span className="muted">
          {rows.filter((x) => x.status === "running").length} of {rows.length}{" "}
          online
        </span>
      </PageHeader>
      {error && <div className="form-error">{error}</div>}
      <Card className="table-card">
        <div className="data-table">
          <div className="table-head">
            <span>Container</span>
            <span>Status</span>
            <span>Uptime</span>
            <span>CPU</span>
            <span>Memory</span>
            <span>Restarts</span>
          </div>
          {rows.map((row) => (
            <div className="table-row" key={row.id}>
              <span className="container-name">
                <i>
                  <Box size={18} />
                </i>
                <span>
                  <b>{row.name}</b>
                  <small>{row.image}</small>
                  {row.last_state_change && (
                    <small>
                      State changed{" "}
                      {new Date(row.last_state_change).toLocaleString()}
                    </small>
                  )}
                </span>
              </span>
              <Status value={row.health ?? row.status} />
              <span>{formatUptime(row.uptime_seconds)}</span>
              <span>{row.cpu_percent?.toFixed(1) ?? "—"}%</span>
              <span>
                {row.memory_bytes ? formatBytes(row.memory_bytes) : "—"}
              </span>
              <span>{row.restart_count}</span>
            </div>
          ))}
        </div>
      </Card>
      <p className="security-note">
        ServerSense exposes only normalized, read-only container telemetry to
        SENSE. The Docker socket is never available to the model.
      </p>
    </div>
  );
}
