import asyncio
from types import SimpleNamespace

import pytest
from pytest import MonkeyPatch

from serversense.services import jobs
from serversense.services.activity import ActiveViewerLease


def test_active_viewer_lease_expires_without_visible_ui_heartbeats() -> None:
    current = [100.0]
    lease = ActiveViewerLease(ttl_seconds=45, clock=lambda: current[0])

    assert lease.is_active() is False
    lease.renew()
    assert lease.is_active() is True

    current[0] = 144.9
    assert lease.is_active() is True
    current[0] = 145.0
    assert lease.is_active() is False


def test_active_viewer_renewal_never_shortens_an_existing_lease() -> None:
    current = [100.0]
    lease = ActiveViewerLease(ttl_seconds=45, clock=lambda: current[0])
    lease.renew()
    current[0] = 120.0
    lease.renew()
    current[0] = 164.9

    assert lease.is_active() is True


def test_active_viewer_enables_five_second_metric_only_cycles(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        active_metrics_interval_seconds=5,
        active_docker_interval_seconds=15,
        metrics_interval_seconds=30,
        docker_interval_seconds=30,
        storage_interval_seconds=3600,
        disk_interval_seconds=900,
    )
    calls: list[tuple[bool, bool, bool, bool, bool]] = []
    sleeps: list[float] = []

    def collect(*args: bool) -> bool:
        calls.append(args)  # type: ignore[arg-type]
        return True

    async def run_inline(function: object, *args: object) -> object:
        return function(*args)  # type: ignore[operator]

    class StopLoop(Exception):
        pass

    async def stop_after_three_cycles(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 4:
            raise StopLoop

    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(jobs.active_viewers, "is_active", lambda: True)
    monkeypatch.setattr(jobs, "collection_cycle", collect)
    monkeypatch.setattr(jobs, "cleanup_cycle", lambda: None)
    monkeypatch.setattr(jobs.asyncio, "to_thread", run_inline)
    monkeypatch.setattr(jobs.asyncio, "sleep", stop_after_three_cycles)

    with pytest.raises(StopLoop):
        asyncio.run(jobs.monitoring_loop())

    assert sleeps == [2, 5, 5, 5]
    assert calls[0] == (True, True, True, True, True)
    assert calls[1:] == [
        (False, False, False, False, False),
        (False, False, False, False, False),
    ]
