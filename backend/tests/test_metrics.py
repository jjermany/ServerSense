from datetime import UTC, datetime, timedelta

from serversense.models import MetricSample
from serversense.services.metrics import calculate_network_rates


def sample(timestamp: datetime, received: int | None, sent: int | None) -> MetricSample:
    return MetricSample(
        timestamp=timestamp,
        network_rx_bytes=received,
        network_tx_bytes=sent,
    )


def test_network_rates_are_derived_from_counter_deltas() -> None:
    now = datetime.now(UTC)
    result = calculate_network_rates(
        sample(now - timedelta(seconds=10), 1_000, 4_000),
        sample(now, 2_500, 4_500),
    )
    assert result == {
        "rx_bytes_per_second": 150.0,
        "tx_bytes_per_second": 50.0,
        "sample_interval_seconds": 10.0,
    }


def test_network_rates_ignore_resets_and_invalid_intervals() -> None:
    now = datetime.now(UTC)
    reset = calculate_network_rates(
        sample(now, 1_000, 500), sample(now + timedelta(seconds=10), 100, 700)
    )
    assert reset["rx_bytes_per_second"] is None
    assert reset["tx_bytes_per_second"] == 20
    assert reset["sample_interval_seconds"] == 10
    invalid = calculate_network_rates(sample(now, 1, 1), sample(now, 2, 2))
    assert invalid["sample_interval_seconds"] is None
    assert calculate_network_rates(None, sample(now, 1, 1))["rx_bytes_per_second"] is None
