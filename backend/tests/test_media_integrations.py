from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from serversense.db import SessionLocal
from serversense.models import Integration, MediaActivity, MediaSchedule, StorageSample
from serversense.services.integrations import collect_integration
from serversense.services.timezones import format_local_datetime
from serversense.services.tools import execute_tool


def test_multiple_named_radarr_instances_and_encrypted_keys(
    authenticated_client: TestClient,
) -> None:
    first = authenticated_client.post(
        "/api/integrations",
        json={
            "provider": "radarr",
            "name": "Movies",
            "url": "http://radarr:7878/",
            "api_key": "movies-secret",
            "enabled": True,
        },
    )
    second = authenticated_client.post(
        "/api/integrations",
        json={
            "provider": "radarr",
            "name": "Anime",
            "url": "http://radarr-anime:7878",
            "api_key": "anime-secret",
            "enabled": True,
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201
    configured = authenticated_client.get("/api/integrations").json()["configured"]
    assert {item["name"] for item in configured} >= {"Movies", "Anime"}
    assert all("api_key" not in item for item in configured)
    with SessionLocal() as db:
        rows = list(
            db.scalars(select(Integration).where(Integration.name.in_(("Movies", "Anime"))))
        )
        assert len(rows) == 2
        assert all("secret" not in str(row.config.get("api_key_encrypted")) for row in rows)


def test_collects_normalized_history_and_deduplicates(
    monkeypatch: object,
) -> None:
    with SessionLocal() as db:
        db.execute(delete(MediaActivity))
        integration = Integration(
            provider="radarr",
            name="Anime",
            enabled=True,
            config={"url": "http://radarr-anime:7878", "api_key_encrypted": "unused"},
        )
        db.add(integration)
        db.commit()
        db.refresh(integration)
        now = datetime.now(UTC)
        imported_at = now - timedelta(hours=1)
        deleted_at = imported_at - timedelta(seconds=1)

        def fake_request(item: Integration, path: str, params: dict | None = None) -> Any:
            assert item.id == integration.id
            if path == "calendar":
                return [
                    {
                        "id": 7,
                        "title": "Upcoming Movie",
                        "digitalRelease": (now + timedelta(days=1)).isoformat(),
                        "monitored": True,
                        "hasFile": False,
                    }
                ]
            assert path == "history"
            return {
                "records": [
                    {
                        "id": 42,
                        "date": imported_at.isoformat(),
                        "eventType": "downloadFolderImported",
                        "sourceTitle": "/private/download/path.mkv",
                        "quality": {"quality": {"name": "Bluray-2160p"}},
                        "data": {"importedPath": "/private/library/path.mkv"},
                        "movie": {"title": "Example Movie", "movieFile": {"size": 1234}},
                    },
                    {
                        "id": 41,
                        "date": deleted_at.isoformat(),
                        "eventType": "movieFileDeleted",
                        "sourceTitle": "/private/library/old-file.mkv",
                        "quality": {"quality": {"name": "WEBDL-1080p"}},
                        "data": {"reason": "Upgrade", "size": "800"},
                        "movie": {"title": "Example Movie"},
                    },
                ]
            }

        from serversense.services import integrations

        monkeypatch.setattr(integrations, "_request", fake_request)  # type: ignore[attr-defined]
        assert collect_integration(db, integration, now) == 2
        integration.config = dict(integration.config) | {"last_collected_at": ""}
        db.commit()
        assert collect_integration(db, integration, now) == 0
        row = db.scalar(
            select(MediaActivity).where(
                MediaActivity.integration_id == integration.id,
                MediaActivity.external_id == "42",
            )
        )
        assert row is not None
        assert row.instance_name == "Anime"
        assert row.title == "Example Movie"
        assert row.quality == "Bluray-2160p"
        assert row.bytes == 1234
        assert row.is_upgrade is False
        assert "private" not in str(row.__dict__)
        upgrades = execute_tool(db, "get_quality_upgrades", {"days": 1, "instance": "Anime"})
        assert upgrades["activities"][0]["previous_quality"] == "WEBDL-1080p"
        assert upgrades["activities"][0]["quality"] == "Bluray-2160p"
        schedule = db.scalar(
            select(MediaSchedule).where(MediaSchedule.integration_id == integration.id)
        )
        assert schedule is not None
        assert schedule.title == "Upcoming Movie"
        assert schedule.release_type == "digital_release"


def test_media_tools_filter_by_instance_and_explain_storage_evidence() -> None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        integration = Integration(provider="radarr", name="Movies", enabled=True, config={})
        db.add(integration)
        db.flush()
        db.add(
            MediaActivity(
                integration_id=integration.id,
                external_id="summary-test",
                occurred_at=now,
                provider="radarr",
                instance_name="Movies",
                event_type="imported",
                media_type="movie",
                title="A Movie",
                parent_title=None,
                season_number=None,
                episode_number=None,
                quality="WEBDL-1080p",
                bytes=500,
                is_upgrade=False,
            )
        )
        db.add_all(
            [
                StorageSample(
                    timestamp=now.replace(hour=max(0, now.hour - 1)),
                    total_bytes=10_000,
                    used_bytes=1_000,
                    free_bytes=9_000,
                    source="test",
                ),
                StorageSample(
                    timestamp=now,
                    total_bytes=10_000,
                    used_bytes=1_750,
                    free_bytes=8_250,
                    source="test",
                ),
            ]
        )
        db.commit()
        summary = execute_tool(db, "get_media_activity_summary", {"days": 1, "instance": "Movies"})
        assert summary["instances"]["Movies"]["events"]["imported"] >= 1
        assert summary["measured_storage_change_bytes"] is not None
        assert "do not prove" in summary["evidence_note"]
        items = execute_tool(
            db,
            "get_media_activity_items",
            {"days": 1, "instance": "Movies", "event_type": "imported", "limit": 10},
        )
        assert any(row["title"] == "A Movie" for row in items["activities"])


def test_quality_upgrades_pair_provider_upgrade_deletion_with_import() -> None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        integration = Integration(provider="sonarr", name="TV upgrades", enabled=True, config={})
        db.add(integration)
        db.flush()
        common = {
            "integration_id": integration.id,
            "provider": "sonarr",
            "instance_name": "TV upgrades",
            "media_type": "episode",
            "title": "The Episode",
            "parent_title": "The Show",
            "season_number": 2,
            "episode_number": 4,
        }
        db.add_all(
            [
                MediaActivity(
                    **common,
                    external_id="upgrade-delete",
                    occurred_at=now,
                    event_type="file_deleted",
                    quality="WEBDL-1080p",
                    bytes=1_000,
                    is_upgrade=True,
                ),
                MediaActivity(
                    **common,
                    external_id="upgrade-import",
                    occurred_at=now + timedelta(seconds=3),
                    event_type="imported",
                    quality="Bluray-2160p",
                    bytes=2_000,
                    is_upgrade=False,
                ),
            ]
        )
        db.commit()

        summary = execute_tool(
            db, "get_media_activity_summary", {"days": 1, "instance": "TV upgrades"}
        )
        assert summary["instances"]["TV upgrades"]["explicit_upgrades"] == 1
        items = execute_tool(
            db,
            "get_quality_upgrades",
            {"days": 1, "instance": "TV upgrades"},
        )
        assert items["activities"] == [
            {
                "timestamp": (now + timedelta(seconds=3)).isoformat(),
                "timestamp_local_display": format_local_datetime(db, now + timedelta(seconds=3)),
                "provider": "sonarr",
                "instance": "TV upgrades",
                "event_type": "quality_upgraded",
                "media_type": "episode",
                "title": "The Episode",
                "series": "The Show",
                "season": 2,
                "episode": 4,
                "previous_quality": "WEBDL-1080p",
                "quality": "Bluray-2160p",
                "bytes": 2_000,
                "evidence": "provider deletion reason Upgrade followed by import",
            }
        ]


def test_upcoming_media_is_normalized_and_not_described_as_guaranteed() -> None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        integration = Integration(provider="sonarr", name="TV calendar", enabled=True, config={})
        db.add(integration)
        db.flush()
        db.add(
            MediaSchedule(
                integration_id=integration.id,
                external_id="episode:99",
                scheduled_at=now + timedelta(hours=2),
                provider="sonarr",
                instance_name="TV calendar",
                media_type="episode",
                title="Tonight's Episode",
                parent_title="The Show",
                season_number=1,
                episode_number=8,
                release_type="airing",
                monitored=True,
                has_file=False,
            )
        )
        db.commit()

        result = execute_tool(
            db, "get_upcoming_media", {"days": 1, "provider": "sonarr", "limit": 10}
        )
        assert any(item["title"] == "Tonight's Episode" for item in result["items"])
        assert any(
            " AM " in item["scheduled_at_local_display"]
            or " PM " in item["scheduled_at_local_display"]
            for item in result["items"]
        )
        assert "not guaranteed scheduled downloads" in result["terminology_note"]


def test_radarr_calendar_prefers_provider_selected_release_date(
    monkeypatch: object,
) -> None:
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    with SessionLocal() as db:
        integration = Integration(
            provider="radarr",
            name="Movie calendar",
            enabled=True,
            config={"url": "http://radarr:7878", "api_key_encrypted": "unused"},
        )
        db.add(integration)
        db.commit()
        db.refresh(integration)

        def fake_request(item: Integration, path: str, params: dict | None = None) -> Any:
            if path == "history":
                return {"records": []}
            assert path == "calendar"
            return [
                {
                    "id": 2026,
                    "title": "Provider-selected movie",
                    "inCinemas": "2026-09-02T00:00:00Z",
                    "digitalRelease": "2026-09-08T00:00:00Z",
                    "physicalRelease": "2026-09-15T00:00:00Z",
                    "releaseDate": "2026-09-08T00:00:00Z",
                    "monitored": True,
                    "hasFile": False,
                }
            ]

        from serversense.services import integrations

        monkeypatch.setattr(integrations, "_request", fake_request)  # type: ignore[attr-defined]
        collect_integration(db, integration, now)
        schedule = db.scalar(
            select(MediaSchedule).where(MediaSchedule.integration_id == integration.id)
        )

        assert schedule is not None
        assert schedule.scheduled_at.replace(tzinfo=UTC) == datetime(2026, 9, 8, tzinfo=UTC)
        assert schedule.release_type == "digital_release"


def test_integration_test_does_not_follow_redirects(
    authenticated_client: TestClient, monkeypatch: object
) -> None:
    created = authenticated_client.post(
        "/api/integrations",
        json={
            "provider": "sonarr",
            "name": "TV",
            "url": "http://sonarr:8989",
            "api_key": "secret",
        },
    ).json()

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        assert kwargs["follow_redirects"] is False
        assert kwargs["headers"] == {"X-Api-Key": "secret", "Accept": "application/json"}
        return httpx.Response(
            200,
            json={"appName": "Sonarr", "version": "4.0"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)  # type: ignore[attr-defined]
    response = authenticated_client.post(f"/api/integrations/{created['id']}/test")
    assert response.status_code == 200
    assert response.json()["detail"] == "Connected to Sonarr 4.0."
