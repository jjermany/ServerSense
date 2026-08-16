import json
import subprocess
from datetime import UTC, datetime
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
                    "model_family": "Toshiba Enterprise Capacity HDD",
                    "model_name": "TOSHIBA Example Disk 1000",
                    "serial_number": "serial-1000",
                    "power_on_time": {"hours": 1234},
                    "temperature": {"current": 39},
                    "smart_status": {"passed": True},
                    "ata_smart_attributes": {
                        "table": [
                            {"id": 5, "raw": {"value": 0}},
                            {"id": 9, "raw": {"value": 999}},
                        ]
                    },
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
    assert result["attributes"]["power_on_hours"] == 1234
    assert result["attributes"]["reallocated_sectors"] == 0
    assert result["manufacturer"] == "Toshiba"
    assert result["model"] == "TOSHIBA Example Disk 1000"
    assert result["serial"] == "serial-1000"

    assert collector._smart("../sda") == {}
    assert len(observed) == 1


def test_smart_uses_model_vendor_fallback_and_rejects_device_errors(
    monkeypatch: MonkeyPatch,
) -> None:
    payloads = iter(
        [
            {
                "device": {"protocol": "ATA"},
                "model_name": "TOSHIBA MD09ACA18TR",
                "serial_number": "4590A001TK2H",
                "smart_status": {"passed": True},
                "power_on_time": {"hours": 6542},
                "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": 0}}]},
            },
            {
                "smartctl": {
                    "messages": [
                        {
                            "string": "Smartctl open device failed: Operation not permitted",
                            "severity": "error",
                        }
                    ]
                }
            },
        ]
    )

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            arguments, 0, stdout=json.dumps(next(payloads)), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    collector = UnraidCollector(Settings(secret_key="collector-test-secret-key"))

    result = collector._smart("sde")
    assert result["manufacturer"] == "Toshiba"
    assert result["model"] == "TOSHIBA MD09ACA18TR"
    assert result["serial"] == "4590A001TK2H"
    assert result["interface"] == "ATA"
    assert result["attributes"]["power_on_hours"] == 6542
    assert collector._smart("sde") == {}


def test_smart_manufacturer_is_unknown_for_unrecognized_model() -> None:
    assert (
        UnraidCollector._smart_manufacturer({"model_family": None, "vendor": None}, "OOS18000G")
        is None
    )
    assert UnraidCollector._smart_manufacturer({}, "PCIe SSD") is None
    assert UnraidCollector._smart_manufacturer({}, "ST18000NM000J") == "Seagate"
    assert UnraidCollector._smart_manufacturer({}, "WDC WD181KRYZ") == "Western Digital"


def test_smart_retries_incomplete_scsi_detection_as_sat(monkeypatch: MonkeyPatch) -> None:
    observed: list[list[str]] = []
    payloads = iter(
        [
            {"device": {"name": "/dev/sde", "type": "scsi", "protocol": "SCSI"}},
            {
                "device": {"name": "/dev/sde", "type": "sat", "protocol": "ATA"},
                "model_name": "TOSHIBA MD09ACA18TR",
                "smart_status": {"passed": True},
            },
        ]
    )

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        observed.append(arguments)
        return subprocess.CompletedProcess(
            arguments, 0, stdout=json.dumps(next(payloads)), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    collector = UnraidCollector(Settings(secret_key="collector-test-secret-key"))

    result = collector._smart("sde")
    assert observed == [
        ["smartctl", "-a", "-j", "/dev/sde"],
        ["smartctl", "-a", "-j", "-d", "sat", "/dev/sde"],
    ]
    assert result["model"] == "TOSHIBA MD09ACA18TR"
    assert result["interface"] == "ATA"


def test_unraid_pool_devices_are_grouped_without_double_counting_filesystem() -> None:
    collector = UnraidCollector(Settings(secret_key="collector-test-secret-key"))
    sections = collector._parse_disk_sections(
        """
["disk1"]
status="DISK_OK"
size="1000"
id="data-one"
["fast"]
poolName="fast"
status="DISK_OK"
fsType="btrfs"
fsSize="800"
fsFree="300"
size="1000"
id="fast-one"
["fast2"]
poolName="fast"
status="DISK_OK"
fsType="btrfs"
fsSize="800"
fsFree="300"
size="1000"
id="fast-two"
["parity2"]
status="DISK_NP"
size="0"
"""
    )
    assert collector._disk_role(sections[0]) == "data"
    assert collector._disk_role(sections[1]) == "pool"
    assert collector._disk_role({"name": "flash"}) == "boot"
    assert collector._is_assigned_disk(sections[0]) is True
    assert collector._is_assigned_disk(sections[-1]) is False
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


def test_unraid_metadata_is_a_safe_fallback_when_smart_is_unavailable() -> None:
    collector = UnraidCollector(Settings(secret_key="collector-test-secret-key"))
    result = collector._normalize_disk(
        {
            "name": "disk1",
            "id": "serial-one",
            "size": "1000",
            "fsFree": "250",
            "status": "DISK_OK",
            "temp": "37",
            "model": "Example Disk",
        },
        datetime.now(UTC),
    )
    assert result["temperature_c"] == 37
    assert result["smart_status"] == "healthy"
