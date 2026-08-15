from datetime import UTC, datetime

from serversense.models import MetricSample


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def calculate_network_rates(
    previous: MetricSample | None, latest: MetricSample | None
) -> dict[str, float | None]:
    result: dict[str, float | None] = {
        "rx_bytes_per_second": None,
        "tx_bytes_per_second": None,
        "sample_interval_seconds": None,
    }
    if previous is None or latest is None:
        return result
    elapsed = (_aware(latest.timestamp) - _aware(previous.timestamp)).total_seconds()
    if elapsed <= 0:
        return result

    result["sample_interval_seconds"] = elapsed
    for direction in ("rx", "tx"):
        old = getattr(previous, f"network_{direction}_bytes")
        new = getattr(latest, f"network_{direction}_bytes")
        if old is not None and new is not None and new >= old:
            result[f"{direction}_bytes_per_second"] = (new - old) / elapsed
    return result
