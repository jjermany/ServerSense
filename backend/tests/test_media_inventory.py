from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from serversense.db import Base, SessionLocal
from serversense.models import (
    Integration,
    MediaActivity,
    MediaInventoryChange,
    MediaInventorySnapshot,
    StorageSample,
)
from serversense.services import integrations, media_inventory
from serversense.services.ai import _required_tool
from serversense.services.integrations import collect_integration
from serversense.services.media_inventory import fetch_inventory, persist_inventory
from serversense.services.tools import execute_tool


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def source(db: Session, provider: str = "radarr") -> Integration:
    item = Integration(
        provider=provider,
        name="Inventory test",
        enabled=True,
        config={"url": "http://media", "api_key_encrypted": "unused"},
    )
    db.add(item)
    db.commit()
    return item


def movie(media_id: int, file_id: int, size: int | None = 100) -> dict[str, Any]:
    return {
        "id": media_id,
        "title": f"Movie {media_id}",
        "hasFile": True,
        "movieFile": {
            "id": file_id,
            "size": size,
            "path": "/private/file.mkv",
            "quality": {"quality": {"name": "HD"}},
        },
    }


def normalized(file_id: int, media_id: int, size: int | None = 100) -> dict[str, Any]:
    return media_inventory.InventoryFile(
        file_id=file_id,
        media_ids=[media_id],
        media_type="movie",
        title=f"Movie {media_id}",
        bytes=size,
        quality="HD",
    ).model_dump()


def test_dashboard_receives_inventory_totals_coverage_and_bounded_examples(db: Session) -> None:
    from serversense.services.dashboard_insights import _facts

    item = source(db)
    now = datetime.now(UTC)
    db.add(
        StorageSample(
            timestamp=now,
            total_bytes=10_000,
            used_bytes=4_000,
            free_bytes=6_000,
            source="unraid_array",
        )
    )
    persist_inventory(
        db,
        item.id,
        {str(i): normalized(i, i, 100) for i in range(1, 13)},
        now - timedelta(days=2),
    )
    db.commit()
    persist_inventory(db, item.id, {}, now - timedelta(hours=1))
    db.commit()
    facts = _facts(db)
    assert facts is not None
    evidence = facts["media_inventory_changes_7_days"]
    instance = evidence["instances"][0]
    assert instance["baseline_covers_requested_start"] is False
    assert instance["changes"]["removed"]["count"] == 12
    assert instance["known_logical_net_bytes"] == -1200
    assert len(evidence["items"]) == 10
    assert evidence["items_truncated"] is True
    assert evidence["omitted_instance_count"] == 0
    # A failed scan preserves observations but must be visible to the model.
    item.config = item.config | {"inventory_status": "failed"}
    db.commit()
    facts = _facts(db)
    assert facts is not None
    assert facts["media_inventory_changes_7_days"]["instances"][0]["status"] == "failed"


@pytest.mark.parametrize("import_size, expected, unknown", [(40, -60, 0), (None, 0, 1)])
def test_upgrade_summary_distinguishes_shrinkage_and_missing_sizes(
    db: Session,
    import_size: int | None,
    expected: int,
    unknown: int,
) -> None:
    item = source(db)
    now = datetime.now(UTC) - timedelta(hours=1)
    for index, (event_type, size) in enumerate([("file_deleted", 100), ("imported", import_size)]):
        db.add(
            MediaActivity(
                integration_id=item.id,
                external_id=str(index),
                occurred_at=now + timedelta(minutes=index),
                provider=item.provider,
                instance_name=item.name,
                event_type=event_type,
                media_type="movie",
                title="Movie 1",
                provider_media_id=1,
                bytes=size,
                is_upgrade=event_type == "file_deleted",
            )
        )
    db.commit()
    summary = execute_tool(db, "get_media_activity_summary", {"days": 7})
    group = summary["instances"][item.name]
    assert group["confirmed_quality_upgrades"] == 1
    assert group["known_upgrade_net_bytes"] == expected
    assert group["upgrade_unknown_size_count"] == unknown


def test_baseline_diff_history_correlation_and_durable_reload(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = source(db)
    now = datetime.now(UTC) - timedelta(hours=4)
    movies = [movie(1, 10), movie(2, 20, 300)]
    history: list[dict[str, Any]] = []

    def request(integration: Integration, path: str, params: dict | None = None) -> Any:
        assert not db.in_transaction(), "network work must finish before opening a transaction"
        assert integration.id == item.id
        if path == "movie":
            return movies
        if path == "history":
            return {"records": history}
        assert path == "calendar"
        return []

    monkeypatch.setattr(integrations, "_request", request)
    assert collect_integration(db, item, now) == 0
    assert db.scalar(select(func.count()).select_from(MediaInventoryChange)) == 0
    db.commit()
    movies[:] = [movie(1, 11, 250), movie(3, 30, 500), movie(4, 40, None)]
    history[:] = [
        {
            "id": 1,
            "movieId": 1,
            "date": (now + timedelta(minutes=25)).isoformat(),
            "eventType": "movieFileDeleted",
            "data": {"reason": "Upgrade", "size": "100"},
            "quality": {"quality": {"name": "HD"}},
            "movie": {"title": "Movie 1"},
        },
        {
            "id": 2,
            "movieId": 1,
            "date": (now + timedelta(minutes=26)).isoformat(),
            "eventType": "downloadFolderImported",
            "data": {"size": "250"},
            "quality": {"quality": {"name": "HD"}},
            "movie": {"title": "Movie 1"},
        },
    ]
    assert collect_integration(db, item, now + timedelta(hours=1)) == 2
    db.expire_all()
    changes = list(db.scalars(select(MediaInventoryChange)))
    assert {row.change_type for row in changes} == {"added", "removed", "replaced"}
    assert sum(row.net_bytes or 0 for row in changes) == 350
    assert "private" not in str(db.get(MediaInventorySnapshot, item.id).files)
    result = execute_tool(
        db, "get_media_inventory_changes", {"instance": item.name, "days": 1, "limit": 1}
    )
    assert result["items_truncated"] is True
    totals = result["instances"][0]["changes"]
    assert totals["added"]["count"] == 2
    assert totals["added"]["unknown_size_count"] == 1
    assert totals["added"]["known_logical_net_bytes"] == 500
    result = execute_tool(db, "get_media_inventory_changes", {"instance": item.name, "days": 1})
    replacement = next(row for row in result["items"] if row["change_type"] == "replaced")
    assert replacement["confirmed_upgrade_history"] is True
    assert replacement["logical_net_bytes"] == 150
    summary = execute_tool(db, "get_media_activity_summary", {"days": 1})
    group = summary["instances"][item.name]
    assert group["known_upgrade_net_bytes"] == 150
    assert group["upgrade_unknown_size_count"] == 0
    assert (
        "AM" in replacement["observed_at_local_display"]
        or "PM" in replacement["observed_at_local_display"]
    )
    # Identical snapshots after a restart/poll create no duplicate changes.
    count = len(changes)
    db.commit()
    collect_integration(db, item, now + timedelta(hours=2))
    assert db.scalar(select(func.count()).select_from(MediaInventoryChange)) == count


@pytest.mark.parametrize("failure", ["http", "malformed", "oversized"])
def test_failed_inventory_preserves_baseline_and_history_polling(
    db: Session, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    item = source(db)
    now = datetime.now(UTC) - timedelta(hours=3)
    persist_inventory(db, item.id, {"1": normalized(1, 1)}, now)
    db.commit()

    def request(integration: Integration, path: str, params: dict | None = None) -> Any:
        assert not db.in_transaction()
        if path == "history":
            return {
                "records": [
                    {
                        "id": 123,
                        "movieId": 1,
                        "eventType": "grabbed",
                        "date": now.isoformat(),
                        "movie": {"title": "Still collected"},
                    }
                ]
            }
        if path == "calendar":
            return []
        if failure == "http":
            raise httpx.ConnectError("private credential detail")
        if failure == "oversized":
            return [movie(1, 1), movie(2, 2)]
        return [{"id": 1, "hasFile": True, "movieFile": {}}]

    if failure == "oversized":
        monkeypatch.setattr(media_inventory, "MAX_FILES", 1)
    monkeypatch.setattr(integrations, "_request", request)
    assert collect_integration(db, item, now + timedelta(hours=1)) == 1
    snapshot = db.get(MediaInventorySnapshot, item.id)
    assert snapshot.files == {"1": normalized(1, 1)}
    assert db.scalar(select(func.count()).select_from(MediaInventoryChange)) == 0
    assert item.config["inventory_status"] == "collection_failed"
    assert "credential" not in str(item.config)


def test_sonarr_multi_episode_file_counts_once_and_rejects_partial_collection(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = source(db, "sonarr")
    episodes = [
        {
            "id": n,
            "title": f"Episode {n}",
            "seasonNumber": 1,
            "episodeNumber": n,
            "hasFile": True,
            "episodeFileId": 50,
            "episodeFile": {"id": 50, "size": 200},
        }
        for n in (1, 2)
    ]

    def request(integration: Integration, path: str, params: dict | None = None) -> Any:
        assert not db.in_transaction()
        if path == "series":
            return [{"id": 1, "title": "Series"}]
        assert params["seriesId"] == 1
        return episodes if path == "episode" else [{"id": 50}]

    monkeypatch.setattr(integrations, "_request", request)
    files = fetch_inventory(item)
    assert len(files) == 1
    assert files["50"]["bytes"] == 200
    assert files["50"]["media_ids"] == [1, 2]
    assert len(files["50"]["episodes"]) == 2
    episodes.clear()
    with pytest.raises(ValueError, match="incomplete_episode_inventory"):
        fetch_inventory(item)


def test_boundary_windows_missing_sizes_and_replacements_are_not_assumed_upgrades(
    db: Session,
) -> None:
    item = source(db)
    now = datetime.now(UTC)
    persist_inventory(db, item.id, {"1": normalized(1, 1)}, now - timedelta(days=2))
    db.commit()
    persist_inventory(db, item.id, {"2": normalized(2, 1, None)}, now - timedelta(hours=1))
    db.commit()
    result = execute_tool(db, "get_media_inventory_changes", {"instance": item.name, "days": 1})
    assert result["instances"][0]["changes"] == {}
    assert result["instances"][0]["boundary_crossing_changes_excluded_from_totals"] == 1
    assert result["items"][0]["logical_net_bytes"] is None
    assert result["items"][0]["confirmed_upgrade_history"] is False
    item.enabled = False
    db.commit()
    assert execute_tool(db, "get_media_inventory_changes", {})["items"] == []


def test_empty_complete_inventory_and_file_metadata_changes(db: Session) -> None:
    item = source(db)
    now = datetime.now(UTC) - timedelta(hours=3)
    persist_inventory(db, item.id, {"1": normalized(1, 1, 0)}, now)
    db.commit()
    persist_inventory(
        db,
        item.id,
        {"1": normalized(1, 1, 0) | {"title": "Renamed title", "quality": "UHD"}},
        now + timedelta(hours=1),
    )
    db.commit()
    assert db.scalar(select(func.count()).select_from(MediaInventoryChange)) == 0
    persist_inventory(db, item.id, {}, now + timedelta(hours=2))
    db.commit()
    removed = db.scalar(select(MediaInventoryChange))
    assert removed.change_type == "removed" and removed.net_bytes == 0


def test_inventory_tool_is_required_and_arguments_are_bounded(db: Session) -> None:
    assert _required_tool("Why did storage decrease today?", []) == "get_media_inventory_changes"
    assert (
        _required_tool("Which movies were deleted this week?", []) == "get_media_inventory_changes"
    )
    assert _required_tool("Which quality upgrades happened today?", []) == "get_quality_upgrades"
    with pytest.raises(ValueError):
        execute_tool(db, "get_media_inventory_changes", {"limit": 101})
    with pytest.raises(ValueError):
        execute_tool(db, "get_media_inventory_changes", {"path": "/config"})


def test_integration_repoint_and_delete_clear_inventory(authenticated_client: TestClient) -> None:
    payload = {
        "provider": "radarr",
        "name": "Inventory lifecycle",
        "url": "http://radarr-old:7878",
        "api_key": "secret",
        "enabled": True,
    }
    response = authenticated_client.post("/api/integrations", json=payload)
    assert response.status_code == 201
    integration_id = response.json()["id"]
    now = datetime.now(UTC) - timedelta(hours=3)
    with SessionLocal() as session:
        persist_inventory(session, integration_id, {"1": normalized(1, 1)}, now)
        session.commit()
        persist_inventory(session, integration_id, {}, now + timedelta(hours=1))
        session.commit()
    # A name-only edit keeps the same evidence and blank credentials preserve the key.
    payload.update({"name": "Renamed inventory lifecycle", "api_key": ""})
    assert (
        authenticated_client.put(f"/api/integrations/{integration_id}", json=payload).status_code
        == 200
    )
    with SessionLocal() as session:
        assert session.get(MediaInventorySnapshot, integration_id) is not None
        assert (
            execute_tool(session, "get_media_inventory_changes", {"instance": payload["name"]})[
                "items"
            ][0]["instance"]
            == payload["name"]
        )
    payload["url"] = "http://radarr-new:7878"
    response = authenticated_client.put(f"/api/integrations/{integration_id}", json=payload)
    assert response.status_code == 200 and response.json()["inventory_status"] == "pending"
    with SessionLocal() as session:
        assert session.get(MediaInventorySnapshot, integration_id) is None
        assert (
            session.scalar(
                select(MediaInventoryChange).where(
                    MediaInventoryChange.integration_id == integration_id
                )
            )
            is None
        )
        persist_inventory(session, integration_id, {"2": normalized(2, 2)}, now)
        session.commit()
    assert authenticated_client.delete(f"/api/integrations/{integration_id}").status_code == 204
    with SessionLocal() as session:
        assert session.get(MediaInventorySnapshot, integration_id) is None


def test_inventory_change_retention_preserves_latest_baseline(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from sqlalchemy.orm import sessionmaker

    from serversense.services import jobs

    item = source(db)
    now = datetime.now(UTC)
    persist_inventory(db, item.id, {"1": normalized(1, 1)}, now - timedelta(days=4))
    db.commit()
    persist_inventory(db, item.id, {}, now - timedelta(days=3))
    db.commit()
    monkeypatch.setattr(jobs, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(retention_days=1))
    jobs.cleanup_cycle()
    db.expire_all()
    assert db.get(MediaInventorySnapshot, item.id) is not None
    assert db.scalar(select(func.count()).select_from(MediaInventoryChange)) == 0
