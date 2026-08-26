from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from serversense.db import SessionLocal
from serversense.models import Alert, DiskSample, Event


def test_health_and_authentication_required(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/activity").status_code == 401


def test_authenticated_ui_can_renew_active_viewer_lease(
    authenticated_client: TestClient,
) -> None:
    assert authenticated_client.post("/api/activity").status_code == 204


def test_alert_acknowledgement_is_persisted(authenticated_client: TestClient) -> None:
    with SessionLocal() as db:
        alert = Alert(
            alert_type="test_acknowledgement",
            severity="warning",
            title="Acknowledge me",
            message="This alert exercises the acknowledgement route.",
            fingerprint=f"test-acknowledgement-{datetime.now(UTC).timestamp()}",
            data={},
        )
        db.add(alert)
        db.commit()
        alert_id = alert.id

    response = authenticated_client.post(f"/api/alerts/{alert_id}/acknowledge")
    assert response.status_code == 200

    rows = authenticated_client.get("/api/alerts").json()
    acknowledged = next(row for row in rows if row["id"] == alert_id)
    assert acknowledged["active"] is True
    assert acknowledged["acknowledged_at"] is not None


def test_alert_dismissal_hides_the_persisted_alert(authenticated_client: TestClient) -> None:
    with SessionLocal() as db:
        alert = Alert(
            alert_type="test_dismissal",
            severity="warning",
            title="Dismiss me",
            message="This alert exercises the dismissal route.",
            fingerprint=f"test-dismissal-{datetime.now(UTC).timestamp()}",
            data={},
        )
        db.add(alert)
        db.commit()
        alert_id = alert.id

    response = authenticated_client.post(f"/api/alerts/{alert_id}/dismiss")
    assert response.status_code == 200
    assert all(row["id"] != alert_id for row in authenticated_client.get("/api/alerts").json())
    assert all(
        row["id"] != alert_id for row in authenticated_client.get("/api/dashboard").json()["alerts"]
    )

    with SessionLocal() as db:
        dismissed = db.get(Alert, alert_id)
        assert dismissed is not None
        assert dismissed.dismissed_at is not None


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
    assert body["system"]["network_rx_bytes_per_second"] == 1_500_000
    assert body["system"]["network_tx_bytes_per_second"] == 300_000
    assert body["server"]["pools"][0]["name"] == "cache"
    assert client.get("/api/storage/pools").json()[0]["device_count"] == 2


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
    discord_url = "https://discord.com/api/webhooks/123/discord-secret"
    pushover_user = "pushover-user-secret"
    pushover_token = "pushover-app-secret"
    smtp_password = "smtp-password-secret"
    saved = authenticated_client.put(
        "/api/settings/alerts",
        json={
            "free_percent_threshold": 12,
            "forecast_days_threshold": 120,
            "temperature_c_threshold": 48,
            "notify_storage_low": True,
            "notify_forecast_low": False,
            "notify_disk_smart": True,
            "notify_disk_temperature": False,
            "notify_container_stopped": True,
            "webhook_enabled": True,
            "webhook_url": secret_url,
            "discord_enabled": True,
            "discord_webhook_url": discord_url,
            "pushover_enabled": True,
            "pushover_user_key": pushover_user,
            "pushover_app_token": pushover_token,
            "email_enabled": True,
            "smtp_host": "smtp.example.invalid",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_username": "alerts@example.invalid",
            "smtp_password": smtp_password,
            "email_from": "alerts@example.invalid",
            "email_to": "admin@example.invalid",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["webhook_configured"] is True
    assert saved.json()["discord_webhook_url_configured"] is True
    assert saved.json()["pushover_user_key_configured"] is True
    assert saved.json()["pushover_app_token_configured"] is True
    assert saved.json()["smtp_password_configured"] is True
    assert saved.json()["notify_forecast_low"] is False
    assert saved.json()["notify_disk_temperature"] is False
    secrets = (secret_url, discord_url, pushover_user, pushover_token, smtp_password)
    assert all(secret not in saved.text for secret in secrets)
    preserved = authenticated_client.put(
        "/api/settings/alerts",
        json={
            "free_percent_threshold": 10,
            "forecast_days_threshold": 90,
            "temperature_c_threshold": 50,
            "notify_storage_low": True,
            "notify_forecast_low": False,
            "notify_disk_smart": True,
            "notify_disk_temperature": False,
            "notify_container_stopped": True,
            "webhook_enabled": True,
            "discord_enabled": True,
            "pushover_enabled": True,
            "email_enabled": True,
            "smtp_host": "smtp.example.invalid",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "email_from": "alerts@example.invalid",
            "email_to": "admin@example.invalid",
        },
    )
    assert preserved.status_code == 200
    assert preserved.json()["webhook_configured"] is True
    assert preserved.json()["discord_webhook_url_configured"] is True
    assert preserved.json()["pushover_app_token_configured"] is True
    assert preserved.json()["smtp_password_configured"] is True
    diagnostics = authenticated_client.get("/api/system/diagnostics")
    assert diagnostics.status_code == 200
    assert diagnostics.headers["content-type"] == "application/zip"
    assert all(secret.encode() not in diagnostics.content for secret in secrets)
    backup = authenticated_client.post("/api/system/backup")
    assert backup.status_code == 200
    assert backup.json()["filename"].endswith(".db")


def test_proactive_ai_setting_and_dashboard_provenance(
    authenticated_client: TestClient,
) -> None:
    saved = authenticated_client.put(
        "/api/settings/ai",
        json={
            "provider": "openai_compatible",
            "model": "local-model",
            "endpoint": "http://local-model.test",
            "context_window": 8192,
            "temperature": 0.2,
            "timeout_seconds": 30,
            "max_tool_calls": 5,
            "proactive_insights": True,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["proactive_insights"] is True

    with SessionLocal() as db:
        alert = db.scalar(select(Alert).where(Alert.active.is_(True)))
        assert alert is not None
        db.add(
            Event(
                timestamp=datetime.now(UTC),
                event_type="sense_alert_explanation",
                severity="warning",
                title="SENSE alert explanation",
                message="The measured SMART alert needs attention; its cause is unknown.",
                data={
                    "source": "model",
                    "provider": "openai_compatible",
                    "model": "local-model",
                    "alert_ids": [alert.id],
                },
            )
        )
        db.commit()

    dashboard = authenticated_client.get("/api/dashboard")
    assert dashboard.status_code == 200
    explanation = next(item for item in dashboard.json()["insights"] if item["source"] == "sense")
    assert explanation["model"] == "local-model"
    assert "cause is unknown" in explanation["message"]
    reset = authenticated_client.put(
        "/api/settings/ai",
        json={"provider": "disabled", "proactive_insights": False},
    )
    assert reset.status_code == 200


def test_dashboard_handles_disks_without_temperature(authenticated_client: TestClient) -> None:
    with SessionLocal() as db:
        disks = list(db.scalars(select(DiskSample)))
        previous = {disk.id: disk.temperature_c for disk in disks}
        for disk in disks:
            disk.temperature_c = None
        db.commit()

    response = authenticated_client.get("/api/dashboard")
    assert response.status_code == 200
    assert not any(item["title"] == "Disk temperatures" for item in response.json()["insights"])

    with SessionLocal() as db:
        for disk_id, temperature in previous.items():
            disk = db.get(DiskSample, disk_id)
            if disk:
                disk.temperature_c = temperature
        db.commit()
