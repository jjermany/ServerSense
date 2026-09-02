import { ArrowLeft, HardDrive, Thermometer } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatBytes } from "../api";
import type { Disk } from "../types";
import { Card, Metric, PageHeader, Status } from "../components/UI";
import { SLOW_REFRESH_INTERVAL_MS, useLiveQuery } from "../hooks/useLiveQuery";
import { formatDate, formatDateTime } from "../timeFormat";
import { useTimeZone } from "../timeZoneContext";

type DiskDetails = Disk & {
  temperature_history: { timestamp: string; temperature_c: number | null }[];
};

export default function DiskDetailsPage() {
  const { timeZone } = useTimeZone();
  const { diskId } = useParams();
  const { data: disk, error } = useLiveQuery<DiskDetails>(
    `/api/disks/${encodeURIComponent(diskId ?? "")}`,
    SLOW_REFRESH_INTERVAL_MS,
  );
  if (error)
    return (
      <div className="page">
        <div className="form-error">{error}</div>
      </div>
    );
  if (!disk) return <div className="page" />;
  const allocated = disk.total_bytes
    ? (disk.used_bytes / disk.total_bytes) * 100
    : 0;
  return (
    <div className="page">
      <Link className="back-link" to="/disks">
        <ArrowLeft size={14} />
        All disks
      </Link>
      <PageHeader eyebrow={disk.role.toUpperCase()} title={disk.name}>
        <span className="muted">
          Sampled {formatDateTime(disk.sampled_at, timeZone)}
        </span>
        <Status value={disk.smart_status} />
      </PageHeader>
      <div className="metrics-grid three">
        <Metric
          label="Capacity"
          value={formatBytes(disk.total_bytes, 1)}
          detail={`${allocated.toFixed(1)}% used`}
        />
        <Metric
          label="Temperature"
          value={
            disk.temperature_c == null
              ? "—"
              : `${disk.temperature_c.toFixed(0)}°C`
          }
          detail="Latest observed value"
        />
        <Metric
          label="Power-on hours"
          value={disk.smart_attributes.power_on_hours?.toLocaleString() ?? "—"}
          detail="Reported by SMART"
        />
      </div>
      <div className="disk-detail-grid">
        <Card className="full-chart">
          <div className="card-head">
            <div>
              <span className="eyebrow">THERMAL HISTORY</span>
              <h2>Disk temperature</h2>
            </div>
            <Thermometer className="muted" />
          </div>
          <div className="chart-wrap tall">
            <ResponsiveContainer>
              <LineChart data={disk.temperature_history}>
                <CartesianGrid stroke="#202633" vertical={false} />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(v) =>
                    formatDate(String(v), timeZone)
                  }
                  stroke="#626d7e"
                />
                <YAxis unit="°" domain={[20, 60]} stroke="#626d7e" />
                <Tooltip
                  contentStyle={{
                    background: "#111722",
                    border: "1px solid #2a3342",
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="temperature_c"
                  name="Temperature"
                  stroke="#ffb454"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card className="hardware-card">
          <span>
            <HardDrive />
          </span>
          <h2>Hardware</h2>
          <dl>
            <div>
              <dt>Manufacturer</dt>
              <dd>{disk.manufacturer ?? "Unknown"}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{disk.model}</dd>
            </div>
            <div>
              <dt>Serial</dt>
              <dd>{disk.serial}</dd>
            </div>
            <div>
              <dt>Interface</dt>
              <dd>{disk.interface ?? "Unknown"}</dd>
            </div>
            <div>
              <dt>Reallocated sectors</dt>
              <dd>{disk.smart_attributes.reallocated_sectors ?? "—"}</dd>
            </div>
          </dl>
          <p>
            Health states are based on reported SMART status and important
            attributes. ServerSense does not invent a proprietary health
            percentage.
          </p>
        </Card>
      </div>
    </div>
  );
}
