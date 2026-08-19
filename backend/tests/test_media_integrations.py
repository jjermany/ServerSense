from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from serversense.db import SessionLocal
from serversense.models import Integration, MediaActivity, StorageSample
from serversense.services.integrations import collect_integration
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

        def fake_request(item: Integration, path: str, params: dict | None = None) -> dict:
            assert item.id == integration.id
            assert path == "history"
            return {
                "records": [
                    {
                        "id": 42,
                        "date": "2026-08-18T15:00:00Z",
                        "eventType": "downloadFolderImported",
                        "sourceTitle": "/private/download/path.mkv",
                        "quality": {"quality": {"name": "Bluray-1080p"}},
                        "data": {"isUpgrade": True, "importedPath": "/private/library/path.mkv"},
                        "movie": {"title": "Example Movie", "movieFile": {"size": 1234}},
                    }
                ]
            }

        from serversense.services import integrations

        monkeypatch.setattr(integrations, "_request", fake_request)  # type: ignore[attr-defined]
        now = datetime(2026, 8, 18, 16, tzinfo=UTC)
        assert collect_integration(db, integration, now) == 1
        integration.config = dict(integration.config) | {"last_collected_at": ""}
        db.commit()
        assert collect_integration(db, integration, now) == 0
        row = db.scalar(select(MediaActivity).where(MediaActivity.integration_id == integration.id))
        assert row is not None
        assert row.instance_name == "Anime"
        assert row.title == "Example Movie"
        assert row.bytes == 1234
        assert row.is_upgrade is True
        assert "private" not in str(row.__dict__)


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
