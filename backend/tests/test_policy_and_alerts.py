from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from serversense.db import SessionLocal
from serversense.models import Alert, DiskSample, StorageSample
from serversense.security import LoginRateLimiter
from serversense.services.alerting import evaluate_alerts
from serversense.services.permissions import (
    ActionRequest,
    ActionRisk,
    PermissionDenied,
    policy,
)
from serversense.services.tools import TOOLS, execute_tool


def test_tool_registry_has_only_read_only_allowlisted_tools() -> None:
    forbidden = {"shell", "command", "delete", "stop", "restart", "write", "execute"}
    assert len(TOOLS) >= 10
    assert not any(word in name for name in TOOLS for word in forbidden)
    with SessionLocal() as db, pytest.raises(ValueError, match="not permitted"):
        execute_tool(db, "execute_shell", {"command": "whoami"})
    with SessionLocal() as db, pytest.raises(ValueError, match="Unexpected argument"):
        execute_tool(db, "get_server_overview", {"command": "whoami"})
    with SessionLocal() as db, pytest.raises(ValueError, match="too long"):
        execute_tool(db, "get_disk_details", {"disk_id": "x" * 121})


def test_login_rate_limiter_blocks_repeated_attempts() -> None:
    limiter = LoginRateLimiter(attempts=2, window_seconds=60)
    limiter.check("test-client")
    limiter.check("test-client")
    with pytest.raises(HTTPException) as error:
        limiter.check("test-client")
    assert error.value.status_code == 429
    limiter.reset("test-client")
    limiter.check("test-client")


def test_sense_cannot_escalate_to_future_state_changing_actions() -> None:
    with pytest.raises(PermissionDenied, match="read-only"):
        policy.authorize(
            ActionRequest(
                principal="sense",
                action="restart_container",
                risk=ActionRisk.STATE_CHANGE,
                confirmed_by_user=True,
            )
        )
    with pytest.raises(PermissionDenied, match="explicit user confirmation"):
        policy.authorize(
            ActionRequest(
                principal="administrator",
                action="restart_container",
                risk=ActionRisk.STATE_CHANGE,
                confirmed_by_user=False,
            )
        )


def test_alert_rules_trigger_from_metrics() -> None:
    with SessionLocal() as db:
        now = datetime.now(UTC)
        for days_ago in range(30, -1, -1):
            used = 7_000_000 + (30 - days_ago) * 90_000
            db.add(
                StorageSample(
                    timestamp=now - timedelta(days=days_ago),
                    total_bytes=10_000_000,
                    used_bytes=used,
                    free_bytes=10_000_000 - used,
                    source="test",
                )
            )
        db.add(
            DiskSample(
                timestamp=now,
                disk_id="hot-disk",
                name="Hot Disk",
                role="data",
                total_bytes=1_000_000,
                used_bytes=500_000,
                temperature_c=56,
                smart_status="warning",
                smart_attributes={},
            )
        )
        db.commit()
        evaluate_alerts(db)
        types = {alert.alert_type for alert in db.query(Alert).all()}
        assert "disk_temperature" in types
        assert "disk_smart" in types
        assert "storage_low" in types
