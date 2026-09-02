from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from serversense.db import SessionLocal
from serversense.models import DiskSample, StorageSample
from serversense.services.tools import execute_tool


def test_sense_array_tools_exclude_incompatible_storage_sources_and_device_capacity() -> None:
    now = datetime.now(UTC) + timedelta(days=30)
    sources = {"scope-test-container-root", "unraid_array"}
    with SessionLocal() as db:
        db.add_all(
            [
                StorageSample(
                    timestamp=now,
                    total_bytes=100,
                    used_bytes=50,
                    free_bytes=50,
                    source="scope-test-container-root",
                ),
                StorageSample(
                    timestamp=now + timedelta(hours=1),
                    total_bytes=1_000,
                    used_bytes=600,
                    free_bytes=400,
                    source="unraid_array",
                ),
                StorageSample(
                    timestamp=now + timedelta(hours=2),
                    total_bytes=1_000,
                    used_bytes=650,
                    free_bytes=350,
                    source="unraid_array",
                ),
                DiskSample(
                    timestamp=now + timedelta(hours=2),
                    disk_id="scope-test-disk",
                    name="Disk 1",
                    role="data",
                    total_bytes=500,
                    used_bytes=300,
                    temperature_c=35,
                    smart_status="healthy",
                    smart_attributes={},
                ),
            ]
        )
        db.commit()
        try:
            capacity_result = execute_tool(db, "get_array_capacity", {})
            assert set(capacity_result) == {"storage"}
            capacity = capacity_result["storage"]
            assert capacity["scope"] == "combined_array_data_disks"
            assert capacity["includes_named_pools"] is False
            assert capacity["total_bytes"] == 1_000
            assert capacity["free_bytes"] == 350

            overview = execute_tool(db, "get_server_overview", {})
            assert "pools" not in overview["platform_state"]

            forecast = execute_tool(db, "get_storage_forecast", {})
            assert forecast["current"]["total_bytes"] == 1_000
            assert forecast["current"]["scope"] == "combined_array_data_disks"

            history = execute_tool(db, "get_storage_history", {"days": 365})
            assert len(history["samples"]) == 2
            assert all(item["total_bytes"] == 1_000 for item in history["samples"])

            disks = execute_tool(db, "get_disk_list", {})["disks"]
            device = next(item for item in disks if item["id"] == "scope-test-disk")
            assert device["scope"] == "physical_device"
            assert device["total_bytes"] == 500
        finally:
            db.execute(delete(StorageSample).where(StorageSample.source.in_(sources)))
            db.execute(delete(DiskSample).where(DiskSample.disk_id == "scope-test-disk"))
            db.commit()
