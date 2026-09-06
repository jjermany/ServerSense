from datetime import UTC, datetime, timedelta

import pytest

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


@pytest.mark.parametrize("growth", [100_000, -100_000, 0])
def test_dense_forecast_retains_trend_despite_outliers(growth: int) -> None:
    now = datetime.now(UTC)
    samples = [
        StorageSample(
            timestamp=now - timedelta(hours=2160 - index),
            total_bytes=100_000_000,
            used_bytes=20_000_000 + growth * index / 24 + (5_000_000 if index % 19 == 0 else 0),
            free_bytes=80_000_000 - growth * index / 24,
        )
        for index in range(2161)
    ]
    result = calculate_forecast(samples, 90)
    assert result.bytes_per_day == pytest.approx(growth)
    assert result.sample_count == 2161
    if growth <= 0:
        assert result.days_remaining is None


def test_tiny_growth_does_not_overflow_exhaustion_date() -> None:
    samples = make_samples(30, 1)
    for sample in samples:
        sample.free_bytes = 10**15
    result = calculate_forecast(samples, 30)
    assert result.days_remaining == 10**15
    assert result.exhaustion_date is None
