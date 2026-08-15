import json
import subprocess
from pathlib import Path

from pytest import MonkeyPatch

from serversense.config import Settings
from serversense.services.collectors import UnraidCollector


def test_smart_json_is_normalized_without_shell(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    observed: list[list[str]] = []

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        observed.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=json.dumps(
                {
                    "device": {"protocol": "SATA"},
                    "model_family": "Example Storage",
                    "temperature": {"current": 39},
                    "smart_status": {"passed": True},
                    "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": 0}}]},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    collector = UnraidCollector(
        Settings(config_dir=tmp_path, secret_key="collector-test-secret-key")
    )
    result = collector._smart("sda")
    assert observed == [["smartctl", "-a", "-j", "/dev/sda"]]
    assert result["status"] == "healthy"
    assert result["temperature"] == 39
    assert result["attributes"]["5"] == 0

    assert collector._smart("../sda") == {}
    assert len(observed) == 1


def test_unraid_pool_devices_are_grouped_without_double_counting_filesystem() -> None:
    collector = UnraidCollector(Settings(secret_key="collector-test-secret-key"))
    sections = collector._parse_disk_sections(
        """
["disk1"]
status="DISK_OK"
size="1000"
["fast"]
poolName="fast"
status="DISK_OK"
fsType="btrfs"
fsSize="800"
fsFree="300"
size="1000"
["fast2"]
poolName="fast"
status="DISK_OK"
fsType="btrfs"
fsSize="800"
fsFree="300"
size="1000"
"""
    )
    assert collector._disk_role(sections[0]) == "data"
    assert collector._disk_role(sections[1]) == "pool"
    assert collector._disk_role({"name": "flash"}) == "boot"
    pools = collector._unraid_pools(sections)
    assert pools == [
        {
            "name": "fast",
            "filesystem": "btrfs",
            "status": "healthy",
            "device_count": 2,
            "devices": ["fast", "fast2"],
            "total_bytes": 800 * 1024,
            "used_bytes": 500 * 1024,
            "free_bytes": 300 * 1024,
            "raw_bytes": 2000 * 1024,
        }
    ]
