from fastapi.testclient import TestClient


def test_saved_timezone_is_used_when_container_tz_is_absent(
    authenticated_client: TestClient, monkeypatch: object
) -> None:
    monkeypatch.delenv("TZ", raising=False)  # type: ignore[attr-defined]

    saved = authenticated_client.put("/api/settings/general", json={"timezone": "America/Chicago"})

    assert saved.status_code == 200
    assert saved.json()["timezone"] == "America/Chicago"
    assert saved.json()["timezone_source"] == "settings"
    assert saved.json()["timezone_configurable"] is True
    assert authenticated_client.get("/api/dashboard").json()["timezone"] == "America/Chicago"


def test_container_tz_takes_precedence_over_saved_setting(
    authenticated_client: TestClient, monkeypatch: object
) -> None:
    monkeypatch.setenv("TZ", "America/New_York")  # type: ignore[attr-defined]

    settings = authenticated_client.get("/api/settings/general")

    assert settings.json()["timezone"] == "America/New_York"
    assert settings.json()["timezone_source"] == "environment"
    assert settings.json()["timezone_configurable"] is False
    assert (
        authenticated_client.put(
            "/api/settings/general", json={"timezone": "America/Denver"}
        ).status_code
        == 409
    )


def test_invalid_timezone_is_rejected(
    authenticated_client: TestClient, monkeypatch: object
) -> None:
    monkeypatch.delenv("TZ", raising=False)  # type: ignore[attr-defined]

    response = authenticated_client.put(
        "/api/settings/general", json={"timezone": "Not/A-Timezone"}
    )

    assert response.status_code == 422
