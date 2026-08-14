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
