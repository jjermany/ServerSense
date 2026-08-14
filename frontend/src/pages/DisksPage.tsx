import { useEffect, useState } from "react";
import { HardDrive, Thermometer } from "lucide-react";
import { Link } from "react-router-dom";
import { api, formatBytes } from "../api";
import type { Disk } from "../types";
import { Card, PageHeader, Status } from "../components/UI";

export default function DisksPage() {
  const [disks, setDisks] = useState<Disk[]>([]);
  useEffect(() => {
    api<Disk[]>("/api/disks").then(setDisks);
  }, []);
  return (
    <div className="page">
      <PageHeader eyebrow="DISK INTELLIGENCE" title="Physical disks">
        <span className="muted">{disks.length} devices observed</span>
      </PageHeader>
      <div className="disk-cards">
        {disks.map((d) => (
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
