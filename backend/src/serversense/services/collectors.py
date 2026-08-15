import json
import logging
import platform
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import docker
import psutil
from docker.errors import DockerException
from sqlalchemy.orm import Session

from serversense.config import Settings
from serversense.models import DiskSample, DockerSample, MetricSample, Setting, StorageSample

logger = logging.getLogger(__name__)


@dataclass
class Snapshot:
    timestamp: datetime
    metric: dict[str, Any]
    storage: dict[str, Any] | None
    disks: list[dict[str, Any]] = field(default_factory=list)
    containers: list[dict[str, Any]] = field(default_factory=list)
    platform: str = "linux"
    state: dict[str, Any] = field(default_factory=dict)


class Collector(ABC):
    @abstractmethod
    def detect(self) -> dict[str, Any]: ...

    @abstractmethod
    def collect(self) -> Snapshot: ...


class LinuxCollector(Collector):
    def __init__(self, settings: Settings):
        self.settings = settings

    def detect(self) -> dict[str, Any]:
        return {
            "platform": "linux",
            "hostname": platform.node(),
            "unraid": Path("/etc/unraid-version").is_file(),
            "array_path": str(self._array_path()),
        }

    def _array_path(self) -> Path:
        return self.settings.array_path if self.settings.array_path.exists() else Path("/")

    def collect(self) -> Snapshot:
        now = datetime.now(UTC)
        memory = psutil.virtual_memory()
        network = psutil.net_io_counters()
        disk = shutil.disk_usage(self._array_path())
        return Snapshot(
            timestamp=now,
            metric={
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_percent": memory.percent,
                "load_1m": psutil.getloadavg()[0] if hasattr(psutil, "getloadavg") else None,
                "uptime_seconds": int(now.timestamp() - psutil.boot_time()),
                "network_rx_bytes": network.bytes_recv,
                "network_tx_bytes": network.bytes_sent,
            },
            storage={
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "source": "system",
            },
            containers=self._docker_containers(),
            state={"array_status": "available", "hostname": platform.node()},
        )

    def _docker_containers(self) -> list[dict[str, Any]]:
        try:
            client = docker.DockerClient(base_url=self.settings.docker_socket, timeout=5)
            result = []
            for container in client.containers.list(all=True):
                state = container.attrs.get("State", {})
                cpu_percent: float | None = None
                memory_bytes: int | None = None
                if state.get("Running"):
                    try:
                        stats = cast(dict[str, Any], container.stats(stream=False))
                        cpu = stats.get("cpu_stats", {})
                        previous = stats.get("precpu_stats", {})
                        cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - previous.get(
                            "cpu_usage", {}
                        ).get("total_usage", 0)
                        system_delta = cpu.get("system_cpu_usage", 0) - previous.get(
                            "system_cpu_usage", 0
                        )
                        cores = (
                            cpu.get("online_cpus")
                            or len(cpu.get("cpu_usage", {}).get("percpu_usage", []))
                            or 1
                        )
                        if system_delta > 0 and cpu_delta >= 0:
                            cpu_percent = cpu_delta / system_delta * cores * 100
                        memory = stats.get("memory_stats", {})
                        memory_bytes = int(memory.get("usage", 0)) or None
                    except DockerException:
                        pass
                started_raw = state.get("StartedAt")
                started_at = None
                if started_raw and not str(started_raw).startswith("0001-"):
                    try:
                        started_at = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
                    except ValueError:
                        pass
                result.append(
                    {
                        "container_id": container.short_id,
                        "name": container.name,
                        "image": container.attrs.get("Config", {}).get("Image", "unknown"),
                        "status": state.get("Status", container.status),
                        "health": state.get("Health", {}).get("Status"),
                        "started_at": started_at,
                        "cpu_percent": cpu_percent,
                        "memory_bytes": memory_bytes,
                        "restart_count": container.attrs.get("RestartCount", 0),
                    }
                )
            client.close()
            return result
        except DockerException as exc:
            logger.info("Docker telemetry unavailable: %s", type(exc).__name__)
            return []


class UnraidCollector(LinuxCollector):
    def detect(self) -> dict[str, Any]:
        data = super().detect()
        data["platform"] = "unraid"
        version_file = Path("/etc/unraid-version")
        if version_file.is_file():
            data["version"] = version_file.read_text(errors="replace").strip()[:200]
        return data

    def collect(self) -> Snapshot:
        snapshot = super().collect()
        snapshot.platform = "unraid"
        sections = self._unraid_disk_sections()
        snapshot.disks = self._unraid_disks(snapshot.timestamp, sections)
        snapshot.state = self._unraid_state()
        snapshot.state["pools"] = self._unraid_pools(sections)
        return snapshot

    def _unraid_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "array_status": "unknown",
            "platform": "unraid",
            "hostname": platform.node(),
        }
        var_file = Path("/var/local/emhttp/var.ini")
        if var_file.is_file():
            values: dict[str, str] = {}
            for raw in var_file.read_text(errors="replace").splitlines():
                if "=" in raw:
                    key, value = raw.split("=", 1)
                    values[key.strip()] = value.strip().strip('"')[:500]
            state.update(
                {
                    "array_status": values.get("mdState", "unknown").lower(),
                    "disabled_disks": int(values.get("mdNumDisabled", "0") or 0),
                    "parity_action": values.get("mdResyncAction", "idle"),
                    "parity_position": int(values.get("mdResyncPos", "0") or 0),
                    "parity_size": int(values.get("mdResyncSize", "0") or 0),
                    "parity_errors": int(values.get("mdSyncErrs", "0") or 0),
                }
            )
        state["ups"] = self._ups_status()
        return state

    def _ups_status(self) -> dict[str, str] | None:
        if not shutil.which("apcaccess"):
            return None
        try:
            result = subprocess.run(
                ["apcaccess", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            allowed = {"STATUS", "BCHARGE", "TIMELEFT", "LINEV", "LOADPCT"}
            values: dict[str, str] = {}
            for raw in result.stdout.splitlines():
                if ":" in raw:
                    key, value = raw.split(":", 1)
                    if key.strip() in allowed:
                        values[key.strip().lower()] = value.strip()[:100]
            return values or None
        except (OSError, subprocess.SubprocessError):
            return None

    def _unraid_disk_sections(self) -> list[dict[str, str]]:
        disk_config = Path("/var/local/emhttp/disks.ini")
        if not disk_config.is_file():
            return []
        return self._parse_disk_sections(disk_config.read_text(errors="replace"))

    @staticmethod
    def _parse_disk_sections(content: str) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for raw in content.splitlines():
            line = raw.strip()
            if line.startswith("["):
                if current:
                    sections.append(current)
                current = {"name": line.strip('[]"')}
            elif "=" in line:
                key, value = line.split("=", 1)
                current[key.strip()] = value.strip().strip('"')
        if current:
            sections.append(current)
        return sections

    def _unraid_disks(
        self, timestamp: datetime, sections: list[dict[str, str]] | None = None
    ) -> list[dict[str, Any]]:
        return [
            self._normalize_disk(item, timestamp)
            for item in (sections if sections is not None else self._unraid_disk_sections())
        ]

    @staticmethod
    def _number(value: str | None) -> int:
        try:
            return max(0, int(value or 0))
        except ValueError:
            return 0

    @staticmethod
    def _disk_role(data: dict[str, str]) -> str:
        name = data.get("name", "").lower()
        if name.startswith("parity"):
            return "parity"
        if re.fullmatch(r"disk\d+", name):
            return "data"
        if name == "flash":
            return "boot"
        return "pool"

    @staticmethod
    def _pool_name(data: dict[str, str]) -> str:
        explicit = data.get("poolName") or data.get("poolname")
        if explicit:
            return explicit[:120]
        return re.sub(r"\d+$", "", data.get("name", "pool"))[:120] or "pool"

    def _unraid_pools(self, sections: list[dict[str, str]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in sections:
            if self._disk_role(item) == "pool":
                grouped.setdefault(self._pool_name(item), []).append(item)

        pools: list[dict[str, Any]] = []
        for name, devices in grouped.items():
            filesystem_sizes = [self._number(item.get("fsSize")) * 1024 for item in devices]
            filesystem_free = [self._number(item.get("fsFree")) * 1024 for item in devices]
            total = max(filesystem_sizes, default=0)
            free = min(max(filesystem_free, default=0), total)
            raw = sum(self._number(item.get("size")) * 1024 for item in devices)
            reported = [item.get("status", "").lower() for item in devices]
            warning = any(
                value and value not in {"disk_ok", "ok", "mounted", "active"} for value in reported
            )
            pools.append(
                {
                    "name": name,
                    "filesystem": next(
                        (
                            item.get("fsType") or item.get("fstype")
                            for item in devices
                            if item.get("fsType") or item.get("fstype")
                        ),
                        None,
                    ),
                    "status": "warning" if warning else "healthy" if total else "unknown",
                    "device_count": len(devices),
                    "devices": [item.get("name", "unknown")[:120] for item in devices],
                    "total_bytes": total,
                    "used_bytes": max(0, total - free),
                    "free_bytes": free,
                    "raw_bytes": raw,
                }
            )
        return sorted(pools, key=lambda item: str(item["name"]).lower())

    def _normalize_disk(self, data: dict[str, str], timestamp: datetime) -> dict[str, Any]:
        device = data.get("device", "")
        smart = self._smart(device) if device else {}
        total = self._number(data.get("size")) * 1024
        free = self._number(data.get("fsFree")) * 1024
        return {
            "timestamp": timestamp,
            "disk_id": data.get("id") or data.get("name", device),
            "name": data.get("name", device),
            "role": self._disk_role(data),
            "manufacturer": smart.get("manufacturer"),
            "model": data.get("model"),
            "serial": data.get("id"),
            "interface": smart.get("interface"),
            "total_bytes": total,
            "used_bytes": max(0, total - free),
            "temperature_c": smart.get("temperature"),
            "smart_status": smart.get("status", "unknown"),
            "smart_attributes": smart.get("attributes", {}),
        }

    def _smart(self, device: str) -> dict[str, Any]:
        if not device.replace("_", "").replace("-", "").isalnum():
            return {}
        try:
            result = subprocess.run(
                ["smartctl", "-a", "-j", f"/dev/{device}"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            payload = json.loads(result.stdout or "{}")
            passed = payload.get("smart_status", {}).get("passed")
            attributes = {
                str(item.get("id")): item.get("raw", {}).get("value")
                for item in payload.get("ata_smart_attributes", {}).get("table", [])
            }
            return {
                "temperature": payload.get("temperature", {}).get("current"),
                "status": "healthy"
                if passed is True
                else "critical"
                if passed is False
                else "unknown",
                "attributes": attributes,
                "manufacturer": payload.get("model_family"),
                "interface": payload.get("device", {}).get("protocol"),
            }
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return {}


def build_collector(settings: Settings) -> Collector:
    return (
        UnraidCollector(settings)
        if Path("/etc/unraid-version").is_file()
        else LinuxCollector(settings)
    )


def persist_snapshot(db: Session, snapshot: Snapshot, include_storage: bool = True) -> None:
    db.add(MetricSample(timestamp=snapshot.timestamp, **snapshot.metric))
    if include_storage and snapshot.storage:
        db.add(StorageSample(timestamp=snapshot.timestamp, **snapshot.storage))
    for disk in snapshot.disks:
        db.add(DiskSample(**disk))
    for container in snapshot.containers:
        db.add(DockerSample(timestamp=snapshot.timestamp, **container))
    state = db.get(Setting, "monitoring_state")
    value = {
        **snapshot.state,
        "platform": snapshot.platform,
        "collected_at": snapshot.timestamp.isoformat(),
    }
    if state:
        state.value = value
    else:
        db.add(Setting(key="monitoring_state", value=value))
    db.commit()
