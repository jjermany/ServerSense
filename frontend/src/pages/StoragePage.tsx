import { useState } from "react";
import {
  Area,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";
import { formatBytes } from "../api";
import type { Pool, StoragePoint } from "../types";
import { Card, Metric, PageHeader, Status } from "../components/UI";
import { SLOW_REFRESH_INTERVAL_MS, useLiveQuery } from "../hooks/useLiveQuery";
import { formatDate, formatDateTime, parseTimestamp } from "../timeFormat";
import { useTimeZone } from "../timeZoneContext";

type Forecast = {
  sampled_at: string;
  current_total_bytes: number;
  current_used_bytes: number;
  current_free_bytes: number;
  forecasts: {
    window_days: number;
    bytes_per_day: number | null;
    days_remaining: number | null;
    exhaustion_date: string | null;
    confidence: string;
    sample_count: number;
  }[];
  recommended_window_days: number | null;
};
type ChartPoint = {
  timestamp: string;
  total_bytes: number;
  used_bytes?: number;
  free_bytes?: number;
  projected: boolean;
  projected_used_bytes?: number;
  projected_free_bytes?: number;
};
const ranges = ["24h", "7d", "30d", "90d", "1y", "all"];
export default function StoragePage() {
  const { timeZone } = useTimeZone();
  const [range, setRange] = useState("90d");
  const { data: history = [], error: historyError } = useLiveQuery<StoragePoint[]>(
    `/api/storage/history?range=${range}`,
    SLOW_REFRESH_INTERVAL_MS,
  );
  const { data: forecast, error: forecastError } = useLiveQuery<Forecast>(
    "/api/storage/forecast",
    SLOW_REFRESH_INTERVAL_MS,
  );
  const { data: pools = [], error: poolsError } = useLiveQuery<Pool[]>(
    "/api/storage/pools",
    SLOW_REFRESH_INTERVAL_MS,
  );
  const error = historyError || forecastError || poolsError;
  const preferred = forecast?.forecasts.find((x) => x.window_days === 30);
  const chartData: ChartPoint[] = history.map((point, index) => ({
    ...point,
    projected_used_bytes:
      index === history.length - 1 ? point.used_bytes : undefined,
    projected_free_bytes:
      index === history.length - 1 ? point.free_bytes : undefined,
  }));
  if (
    forecast &&
    preferred?.bytes_per_day &&
    preferred.days_remaining &&
    history.length
  ) {
    const horizon = Math.min(preferred.days_remaining, 180);
    const start = parseTimestamp(history[history.length - 1].timestamp);
    for (let index = 1; index <= 12; index += 1) {
      const days = (horizon / 12) * index;
      const projectedUsed = Math.min(
        forecast.current_total_bytes,
        forecast.current_used_bytes + preferred.bytes_per_day * days,
      );
      chartData.push({
        timestamp: new Date(start.getTime() + days * 86_400_000).toISOString(),
        total_bytes: forecast.current_total_bytes,
        projected: true,
        projected_used_bytes: projectedUsed,
        projected_free_bytes: forecast.current_total_bytes - projectedUsed,
      });
    }
  }
  return (
    <div className="page">
      <PageHeader eyebrow="STORAGE INTELLIGENCE" title="Array capacity">
        {forecast && (
          <span className="muted">
            Sampled {formatDateTime(forecast.sampled_at, timeZone)}
          </span>
        )}
      </PageHeader>
      {error && <div className="form-error">{error}</div>}
      <div className="metrics-grid three">
        <Metric
          label="Available"
          value={forecast ? formatBytes(forecast.current_free_bytes, 2) : "—"}
          detail="Current free capacity"
        />
        <Metric
          label="30-day growth"
          value={
            preferred?.bytes_per_day
              ? `${formatBytes(preferred.bytes_per_day)}/day`
              : "Learning"
          }
          detail={`${preferred?.confidence ?? "Insufficient"} confidence`}
        />
        <Metric
          label="Projected exhaustion"
          value={
            preferred?.days_remaining
              ? `~${Math.round(preferred.days_remaining)} days`
              : "Not projected"
          }
          detail={
            preferred?.exhaustion_date
              ? formatDate(preferred.exhaustion_date, timeZone)
              : "Needs more history"
          }
        />
      </div>
      <Card className="full-chart">
        <div className="card-head">
          <div>
            <span className="eyebrow">CAPACITY HISTORY</span>
            <h2>Measured storage use</h2>
          </div>
          <div className="range-tabs">
            {ranges.map((item) => (
              <button
                key={item}
                className={range === item ? "active" : ""}
                onClick={() => setRange(item)}
              >
                {item.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <div className="chart-wrap tall">
          <ResponsiveContainer>
            <ComposedChart data={chartData}>
              <CartesianGrid stroke="#202633" vertical={false} />
              <XAxis
                dataKey="timestamp"
                tickFormatter={(v) =>
                  formatDate(String(v), timeZone)
                }
                stroke="#626d7e"
              />
              <YAxis
                tickFormatter={(v) => `${(v / 1e12).toFixed(0)}T`}
                stroke="#626d7e"
              />
              <Tooltip
                contentStyle={{
                  background: "#111722",
                  border: "1px solid #2a3342",
                }}
                formatter={(v) => formatBytes(Number(v), 2)}
              />
              <Area
                dataKey="used_bytes"
                name="Used"
                stroke="#8090ff"
                fill="#6c7cff25"
              />
              <Line
                dataKey="free_bytes"
                name="Free"
                stroke="#42d6a4"
                dot={false}
              />
              <Line
                dataKey="projected_used_bytes"
                name="Projected used"
                stroke="#a58aff"
                strokeDasharray="6 5"
                dot={false}
              />
              <Line
                dataKey="projected_free_bytes"
                name="Projected free"
                stroke="#42d6a4"
                strokeDasharray="6 5"
                dot={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div className="chart-foot">
          <span>
            <i className="legend measured" />
            Measured
          </span>
          <span>
            <i className="legend projected" />
            Deterministic 30-day projection
          </span>
        </div>
      </Card>
      {pools.length > 0 && (
        <section aria-labelledby="pool-heading">
          <div className="section-heading">
            <div>
              <span className="eyebrow">UNRAID POOLS</span>
              <h2 id="pool-heading">Storage pools</h2>
            </div>
          </div>
          <div className="forecast-grid">
            {pools.map((pool) => {
              const usedPercent = pool.total_bytes
                ? (pool.used_bytes / pool.total_bytes) * 100
                : 0;
              return (
                <Card key={pool.name} className="forecast-card">
                  <span className="eyebrow">{pool.filesystem ?? "FILESYSTEM UNKNOWN"}</span>
                  <strong>{pool.name}</strong>
                  <div>
                    <span>Status</span>
                    <Status value={pool.status} />
                  </div>
                  <div>
                    <span>Usable capacity</span>
                    <b>{formatBytes(pool.total_bytes, 2)}</b>
                  </div>
                  <div>
                    <span>Used</span>
                    <b>{usedPercent.toFixed(1)}%</b>
                  </div>
                  <div>
                    <span>Devices</span>
                    <b>{pool.device_count}</b>
                  </div>
                </Card>
              );
            })}
          </div>
        </section>
      )}
      <div className="forecast-grid">
        {forecast?.forecasts.map((item) => (
          <Card key={item.window_days} className="forecast-card">
            <span className="eyebrow">{item.window_days}-DAY WINDOW</span>
            <strong>
              {item.bytes_per_day == null
                ? "Learning"
                : `${formatBytes(item.bytes_per_day)}/day`}
            </strong>
            <div>
              <span>Estimated remaining</span>
              <b>
                {item.days_remaining
                  ? `${Math.round(item.days_remaining)} days`
                  : "—"}
              </b>
            </div>
            <div>
              <span>Confidence</span>
              <b>{item.confidence}</b>
            </div>
            <div>
              <span>Samples analyzed</span>
              <b>{item.sample_count}</b>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
