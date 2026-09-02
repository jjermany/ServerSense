import { useEffect } from "react";
import { HardDrive, Thermometer } from "lucide-react";
import { Link } from "react-router-dom";
import { formatBytes } from "../api";
import type { Disk } from "../types";
import { Card, PageHeader, Status } from "../components/UI";
import {
  SLOW_REFRESH_INTERVAL_MS,
  useLiveQuery,
} from "../hooks/useLiveQuery";
import { formatDateTime } from "../timeFormat";
import { useTimeZone } from "../timeZoneContext";

export default function DisksPage() {
  const { timeZone } = useTimeZone();
  const { data: disks, error, refresh } = useLiveQuery<Disk[]>(
    "/api/disks",
    SLOW_REFRESH_INTERVAL_MS,
  );
  useEffect(() => {
    if (!disks || disks.length > 0) return;
    const retry = window.setTimeout(refresh, 3_000);
    return () => window.clearTimeout(retry);
  }, [disks, refresh]);
  const loading = disks === undefined;
  const rows = disks ?? [];
  return (
    <div className="page">
      <PageHeader eyebrow="DISK INTELLIGENCE" title="Physical disks">
        <span className="muted">
          {loading
            ? "Loading disk telemetryâ€¦"
            : rows.length
              ? `${rows.length} devices observed · sampled ${formatDateTime(rows[0].sampled_at, timeZone)}`
              : "Waiting for the first telemetry sampleâ€¦"}
        </span>
      </PageHeader>
      {error && <div className="form-error">{error}</div>}
      <div className="disk-cards">
        {rows.map((d) => (
          <Link
            className="disk-card-link"
            to={`/disks/${encodeURIComponent(d.id)}`}
            key={d.id}
          >
            <Card className="disk-card">
              <div className="disk-title">
                <span>
                  <HardDrive />
                </span>
                <div>
                  <h2>{d.name}</h2>
                  <small>{d.role.toUpperCase()}</small>
                </div>
                <Status value={d.smart_status} />
              </div>
              <div className="capacity">
                <div>
                  <strong>{formatBytes(d.used_bytes, 1)}</strong>
                  <span> used of {formatBytes(d.total_bytes, 0)}</span>
                </div>
                <b>
                  {d.total_bytes
                    ? ((d.used_bytes / d.total_bytes) * 100).toFixed(0)
                    : 0}
                  %
                </b>
              </div>
              <div className="bar large">
                <i
                  style={{
                    width: `${d.total_bytes ? (d.used_bytes / d.total_bytes) * 100 : 0}%`,
                  }}
                />
              </div>
              <dl>
                <div>
                  <dt>Temperature</dt>
                  <dd>
                    <Thermometer size={15} />
                    {d.temperature_c ?? "—"}°C
                  </dd>
                </div>
                <div>
                  <dt>Manufacturer</dt>
                  <dd>{d.manufacturer ?? "—"}</dd>
                </div>
                <div>
                  <dt>Model</dt>
                  <dd>{d.model}</dd>
                </div>
                <div>
                  <dt>Interface</dt>
                  <dd>{d.interface ?? "—"}</dd>
                </div>
                <div>
                  <dt>Serial</dt>
                  <dd>{d.serial}</dd>
                </div>
                <div>
                  <dt>Power-on hours</dt>
                  <dd>
                    {d.smart_attributes.power_on_hours?.toLocaleString() ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt>Reallocated sectors</dt>
                  <dd
                    className={
                      d.smart_attributes.reallocated_sectors
                        ? "warning-text"
                        : ""
                    }
                  >
                    {d.smart_attributes.reallocated_sectors ?? "—"}
                  </dd>
                </div>
              </dl>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
