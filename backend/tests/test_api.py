from fastapi.testclient import TestClient


def test_health_and_authentication_required(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/dashboard").status_code == 401


def test_first_run_setup_and_dashboard(client: TestClient) -> None:
    if not client.get("/api/auth/status").json()["setup_required"]:
        return
    weak = client.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "short", "server_name": "Tower"},
    )
    assert weak.status_code == 422
    response = client.post(
        "/api/auth/setup",
        json={
            "username": "administrator",
            "password": "correct horse battery staple",
            "server_name": "Test Tower",
            "demo_mode": True,
        },
    )
    assert response.status_code == 201
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["demo_mode"] is True
    assert body["storage"]["used_bytes"] > 0
    assert len(body["disks"]) >= 4


def test_persistence_and_login_after_client_restart(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["setup_required"]:
        client.post(
            "/api/auth/setup",
            json={
                "username": "administrator",
                "password": "correct horse battery staple",
                "server_name": "Test Tower",
                "demo_mode": True,
            },
        )
    client.post("/api/auth/logout")
    assert client.get("/api/dashboard").status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "administrator", "password": "correct horse battery staple"},
        ).status_code
        == 200
    )
    assert client.get("/api/storage/history?range=all").json()


def test_forecast_and_safe_chat(authenticated_client: TestClient) -> None:
    forecast = authenticated_client.get("/api/storage/forecast")
    assert forecast.status_code == 200
    window = next(item for item in forecast.json()["forecasts"] if item["window_days"] == 30)
    assert window["bytes_per_day"] > 0
    response = authenticated_client.post(
        "/api/ai/chat", json={"message": "How long until I run out of storage?"}
    )
    assert response.status_code == 200
    assert response.json()["tools_used"] == ["get_storage_forecast"]
    assert "days" in response.json()["message"]


def test_streaming_chat_reports_activity(authenticated_client: TestClient) -> None:
    with authenticated_client.stream(
        "POST",
        "/api/ai/chat/stream",
        json={"message": "Are my disks healthy?"},
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: activity" in body
    assert "event: message" in body
    assert "get_disk_smart_health" in body


def test_alert_settings_hide_secrets_and_diagnostics_are_sanitized(
    authenticated_client: TestClient,
) -> None:
    secret_url = "https://example.invalid/hook/super-secret-token"
    saved = authenticated_client.put(
        "/api/settings/alerts",
        json={
            "free_percent_threshold": 12,
            "forecast_days_threshold": 120,
            "temperature_c_threshold": 48,
            "webhook_enabled": True,
            "webhook_url": secret_url,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["webhook_configured"] is True
    assert secret_url not in saved.text
    diagnostics = authenticated_client.get("/api/system/diagnostics")
    assert diagnostics.status_code == 200
    assert diagnostics.headers["content-type"] == "application/zip"
    assert secret_url.encode() not in diagnostics.content
    backup = authenticated_client.post("/api/system/backup")
    assert backup.status_code == 200
    assert backup.json()["filename"].endswith(".db")
