import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  ArrowUpRight,
  Bot,
  HardDrive,
  Server,
} from "lucide-react";
import { api, formatBytes, formatRate } from "../api";
import type { Dashboard, StoragePoint } from "../types";
import { Card, Metric, PageHeader, Status } from "../components/UI";
import { useLiveQuery } from "../hooks/useLiveQuery";
import {
  formatDate,
  formatDateTime,
  formatTime,
  localHour,
} from "../timeFormat";
import { useTimeZone } from "../timeZoneContext";

export default function DashboardPage() {
  const { timeZone: configuredTimeZone } = useTimeZone();
  const { data, error: liveError } = useLiveQuery<Dashboard>("/api/dashboard");
  const [history, setHistory] = useState<StoragePoint[]>([]);
  const [historyError, setHistoryError] = useState("");
  useEffect(() => {
    let active = true;
    api<StoragePoint[]>("/api/storage/history?range=90d")
      .then((points) => {
        if (active) setHistory(points);
      })
      .catch((reason) => {
        if (active) {
          setHistoryError(reason instanceof Error ? reason.message : "Unable to load history");
        }
      });
    return () => {
      active = false;
    };
  }, []);
  const error = liveError || historyError;
  if (error)
    return (
      <div className="page">
        <div className="form-error">{error}</div>
      </div>
    );
  if (!data)
    return (
      <div className="page loading-grid">
        <Card />
        <Card />
        <Card />
      </div>
    );
  const usedPercent = data.storage.total_bytes
    ? (data.storage.used_bytes / data.storage.total_bytes) * 100
    : 0;
  const hottest = [...data.disks].sort(
    (a, b) => (b.temperature_c ?? 0) - (a.temperature_c ?? 0),
  )[0];
  const online = data.containers.filter((x) => x.status === "running").length;
  const timeZone = data.timezone || configuredTimeZone;
  const currentHour = localHour(new Date(), timeZone);
  return (
    <div className="page">
      <PageHeader
        eyebrow="OVERVIEW"
        title={`Good ${currentHour < 12 ? "morning" : currentHour < 18 ? "afternoon" : "evening"}.`}
      >
        <div className="dashboard-header-status">
          <small className="overview-updated">
            Updated{" "}
            {data.updated_at
              ? formatDateTime(data.updated_at, timeZone)
              : "time unavailable"}
          </small>
          <div className="server-pill">
            <Server size={17} />
            <span>
              <b>{data.server.name}</b>
              <small>
                Array <Status value={data.server.array_status} />
              </small>
            </span>
          </div>
        </div>
      </PageHeader>
      {data.demo_mode && (
        <div className="demo-banner">
          <span>DEMO</span> You’re viewing realistic simulated Unraid telemetry.
        </div>
      )}
      <div className="metrics-grid">
        <Metric
          label="Free storage"
          value={formatBytes(data.storage.free_bytes, 2)}
          detail={`${usedPercent.toFixed(1)}% array used`}
        />
        <Metric
          label="Estimated remaining"
          value={
            data.storage.days_remaining
              ? `~${Math.round(data.storage.days_remaining)} days`
              : "Learning"
          }
          detail="30-day measured trend"
          tone={
            data.storage.days_remaining && data.storage.days_remaining < 180
              ? "warning"
              : "neutral"
          }
        />
        <Metric
          label="Hottest disk"
          value={hottest?.temperature_c ? `${hottest.temperature_c}°C` : "—"}
          detail={hottest?.name}
        />
        <Metric
          label="Docker online"
          value={`${online} / ${data.containers.length}`}
          detail={
            online === data.containers.length
              ? "All containers healthy"
              : "Attention required"
          }
          tone={online < data.containers.length ? "warning" : "neutral"}
        />
      </div>
      <div className="metrics-grid three">
        <Metric
          label="System load"
          value={
            data.system.cpu_percent == null
              ? "Learning"
              : `${data.system.cpu_percent.toFixed(1)}% CPU`
          }
          detail={
            data.system.memory_percent == null
              ? "Memory sample unavailable"
              : `${data.system.memory_percent.toFixed(1)}% memory used · sampled ${
                  data.system.sampled_at
                    ? formatTime(data.system.sampled_at, timeZone)
                    : "time unavailable"
                }`
          }
        />
        <Metric
          label="Network received"
          value={formatRate(data.system.network_rx_bytes_per_second)}
          detail="Between the latest samples"
        />
        <Metric
          label="Network sent"
          value={formatRate(data.system.network_tx_bytes_per_second)}
          detail="Between the latest samples"
        />
      </div>
      <div className="dashboard-grid">
        <Card className="storage-chart">
          <div className="card-head">
            <div>
              <span className="eyebrow">ARRAY STORAGE</span>
              <h2>Capacity trend</h2>
            </div>
            <div className="big-value">
              <strong>{formatBytes(data.storage.free_bytes, 2)}</strong>
              <small>available</small>
            </div>
          </div>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={history}>
                <defs>
                  <linearGradient id="used" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#6c7cff" stopOpacity={0.45} />
                    <stop
                      offset="100%"
                      stopColor="#6c7cff"
                      stopOpacity={0.02}
                    />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#202633" vertical={false} />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(v) =>
                    formatDate(String(v), timeZone)
                  }
                  stroke="#626d7e"
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  tickFormatter={(v) => `${(v / 1e12).toFixed(0)}T`}
                  domain={[
                    "dataMin - 1000000000000",
                    "dataMax + 1000000000000",
                  ]}
                  stroke="#626d7e"
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  contentStyle={{
                    background: "#111722",
                    border: "1px solid #2a3342",
                    borderRadius: 10,
                  }}
                  formatter={(v) => formatBytes(Number(v), 2)}
                labelFormatter={(v) => formatDateTime(String(v), timeZone)}
                />
                <Area
                  dataKey="used_bytes"
                  name="Used"
                  stroke="#8090ff"
                  strokeWidth={2}
                  fill="url(#used)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="chart-foot">
            <span>
              <i className="legend measured" />
              Measured usage
            </span>
            <span className="growth">
              +{formatBytes(data.storage.growth_bytes_per_day ?? 0)} / day{" "}
              <ArrowUpRight size={14} />
            </span>
          </div>
        </Card>
        <Card className="insight-card">
          <div className="sense-heading">
            <span>
              <Bot size={19} />
            </span>
            <div>
              <small>SENSE</small>
              <b>Insight</b>
            </div>
            <i className="spark" />
          </div>
          {data.insights.map((insight, index) => (
            <article key={index}>
              <h3>{insight.title}</h3>
              <p>{insight.message}</p>
              <small>
                <i
                  className={`status-dot ${insight.severity === "warning" ? "warn" : "good"}`}
                />{" "}
                {insight.source === "sense"
                  ? `${insight.kind === "dashboard_summary" ? "Cached SENSE summary" : "SENSE explanation"} · ${insight.model ?? "configured model"}`
                  : "Based on measured telemetry"}
                {insight.generated_at && (
                  <>
                    {" "}· {insight.source === "sense" ? "Generated" : "Measured"}{" "}
                    {formatDateTime(insight.generated_at, timeZone)}
                  </>
                )}
              </small>
            </article>
          ))}
        </Card>
      </div>
      <div className="lower-grid">
        <Card>
          <div className="card-head">
            <div>
              <span className="eyebrow">PHYSICAL STORAGE</span>
              <h2>Disk health</h2>
            </div>
            <a href="/disks">View all</a>
          </div>
          <div className="disk-list">
            {data.disks.slice(0, 5).map((disk) => (
              <div className="disk-row" key={disk.id}>
                <HardDrive size={19} />
                <div>
                  <b>{disk.name}</b>
                  <small>
                    {disk.role} · {formatBytes(disk.total_bytes, 0)}
                  </small>
                </div>
                <div className="bar">
                  <i
                    style={{
                      width: `${(disk.used_bytes / disk.total_bytes) * 100}%`,
                    }}
                  />
                </div>
                <span>{disk.temperature_c ?? "—"}°</span>
                <Status value={disk.smart_status} />
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <div className="card-head">
            <div>
              <span className="eyebrow">RECENT ACTIVITY</span>
              <h2>Alerts</h2>
            </div>
            <a href="/alerts">View all</a>
          </div>
          {data.alerts.length ? (
            <div className="alert-list">
              {data.alerts.map((alert) => (
                <article key={alert.id}>
                  <span className={`alert-icon ${alert.severity}`}>
                    <AlertTriangle size={16} />
                  </span>
                  <div>
                    <b>{alert.title}</b>
                    <p>{alert.message}</p>
                    <small>{formatDateTime(alert.created_at, timeZone)}</small>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="empty">No active alerts</div>
          )}
        </Card>
      </div>
    </div>
  );
}
