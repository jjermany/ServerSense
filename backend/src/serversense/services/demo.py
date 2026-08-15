import math
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from serversense.models import Alert, DiskSample, DockerSample, MetricSample, Setting, StorageSample

TB = 10**12


def seed_demo_data(db: Session) -> None:
    if db.scalar(select(func.count(StorageSample.id)).where(StorageSample.source == "demo")) or 0:
        return
    rng = random.Random(8675309)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    if not db.get(Setting, "monitoring_state"):
        db.add(
            Setting(
                key="monitoring_state",
                value={
                    "platform": "unraid-demo",
                    "array_status": "started",
                    "disabled_disks": 0,
                    "parity_action": "idle",
                    "parity_errors": 0,
                    "ups": {"status": "ONLINE", "bcharge": "100 Percent"},
                    "pools": [
                        {
                            "name": "cache",
                            "filesystem": "btrfs",
                            "status": "healthy",
                            "device_count": 2,
                            "devices": ["cache", "cache2"],
                            "total_bytes": 2 * TB,
                            "used_bytes": int(1.4 * TB),
                            "free_bytes": int(0.6 * TB),
                            "raw_bytes": 4 * TB,
                        }
                    ],
                    "collected_at": now.isoformat(),
                },
            )
        )
    total = 72 * TB
    starting_used = 57.4 * TB
    for day in range(120, -1, -1):
        timestamp = now - timedelta(days=day)
        trend = (120 - day) * 31.5 * 10**9
        noise = math.sin(day / 5) * 90 * 10**9 + rng.uniform(-35, 35) * 10**9
        used = int(starting_used + trend + noise)
        db.add(
            StorageSample(
                timestamp=timestamp,
                total_bytes=total,
                used_bytes=used,
                free_bytes=total - used,
                source="demo",
            )
        )

    disks: list[tuple[str, str, str, int, float, int, str]] = [
        ("parity", "Parity", "parity", 18, 0, 36, "healthy"),
        ("disk1", "Disk 1", "data", 18, 14.8, 39, "healthy"),
        ("disk2", "Disk 2", "data", 18, 16.2, 43, "warning"),
        ("disk3", "Disk 3", "data", 18, 15.9, 38, "healthy"),
        ("cache", "Cache Pool", "pool", 2, 1.4, 41, "healthy"),
    ]
    for disk_id, name, role, size, used_tb, temp, health in disks:
        for days_ago in range(30, 0, -1):
            db.add(
                DiskSample(
                    timestamp=now - timedelta(days=days_ago),
                    disk_id=disk_id,
                    name=name,
                    role=role,
                    manufacturer="ServerSense Labs",
                    model="ServerSense Virtual Disk",
                    serial=f"DEMO-{disk_id.upper()}",
                    interface="SATA",
                    total_bytes=size * TB,
                    used_bytes=int(used_tb * TB),
                    temperature_c=temp + math.sin(days_ago / 3) * 2,
                    smart_status=health,
                    smart_attributes={
                        "power_on_hours": 18420 - days_ago * 24,
                        "reallocated_sectors": 2 if health == "warning" else 0,
                    },
                )
            )
        db.add(
            DiskSample(
                timestamp=now,
                disk_id=disk_id,
                name=name,
                role=role,
                manufacturer="ServerSense Labs",
                model="ServerSense Virtual Disk",
                serial=f"DEMO-{disk_id.upper()}",
                interface="SATA",
                total_bytes=size * TB,
                used_bytes=int(used_tb * TB),
                temperature_c=temp,
                smart_status=health,
                smart_attributes={
                    "power_on_hours": 18420,
                    "reallocated_sectors": 2 if health == "warning" else 0,
                },
            )
        )
    containers = [
        ("plex", "Plex", "plexinc/pms-docker", "running", "healthy", 1.8, 2.1e9, 0),
        ("sonarr", "Sonarr", "linuxserver/sonarr", "running", "healthy", 0.3, 320e6, 0),
        ("radarr", "Radarr", "linuxserver/radarr", "running", "healthy", 0.4, 340e6, 0),
        ("sab", "SABnzbd", "linuxserver/sabnzbd", "running", "healthy", 3.2, 810e6, 1),
        ("backup", "Backup", "restic/rest-server", "exited", None, 0, 0, 0),
    ]
    for values in containers:
        db.add(
            DockerSample(
                timestamp=now - timedelta(days=1),
                container_id=values[0],
                name=values[1],
                image=values[2],
                status="running",
                health="healthy" if values[4] else None,
                started_at=now - timedelta(days=14),
                cpu_percent=values[5],
                memory_bytes=int(values[6]),
                restart_count=0,
            )
        )
        db.add(
            DockerSample(
                timestamp=now,
                container_id=values[0],
                name=values[1],
                image=values[2],
                status=values[3],
                health=values[4],
                started_at=now - timedelta(days=14) if values[3] == "running" else None,
                cpu_percent=values[5],
                memory_bytes=int(values[6]),
                restart_count=values[7],
            )
        )
    db.add(
        MetricSample(
            timestamp=now - timedelta(minutes=5),
            cpu_percent=17.9,
            memory_percent=61.1,
            load_1m=1.18,
            uptime_seconds=1_284_180,
            network_rx_bytes=8_000_000_000,
            network_tx_bytes=3_000_000_000,
        )
    )
    db.add(
        MetricSample(
            timestamp=now,
            cpu_percent=18.7,
            memory_percent=61.4,
            load_1m=1.22,
            uptime_seconds=1_284_480,
            network_rx_bytes=8_450_000_000,
            network_tx_bytes=3_090_000_000,
        )
    )
    db.add(
        Alert(
            alert_type="disk_smart",
            severity="warning",
            title="Disk 2 SMART warning",
            message="Disk 2 reports 2 reallocated sectors. Monitor for further changes.",
            fingerprint="demo-disk2-smart",
            data={"disk_id": "disk2"},
        )
    )
    db.commit()
