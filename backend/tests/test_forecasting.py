from datetime import UTC, datetime, timedelta

from serversense.models import StorageSample
from serversense.services.forecasting import calculate_forecast


def make_samples(days: int, growth_per_day: int) -> list[StorageSample]:
    now = datetime.now(UTC)
    total = 10_000_000
    return [
        StorageSample(
            timestamp=now - timedelta(days=days - index),
            total_bytes=total,
            used_bytes=1_000_000 + index * growth_per_day,
            free_bytes=total - 1_000_000 - index * growth_per_day,
        )
        for index in range(days + 1)
    ]


def test_forecast_uses_deterministic_robust_slope() -> None:
    forecast = calculate_forecast(make_samples(30, 100_000), 30)
    assert forecast.bytes_per_day == 100_000
    assert forecast.days_remaining == 60
    assert forecast.confidence == "High"


def test_forecast_requires_history_and_handles_decline() -> None:
    assert calculate_forecast(make_samples(1, 100), 30).bytes_per_day is None
    declining = calculate_forecast(make_samples(30, -100), 30)
    assert declining.days_remaining is None
