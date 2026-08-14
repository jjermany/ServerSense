from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median

from serversense.models import StorageSample


@dataclass(frozen=True)
class Forecast:
    window_days: int
    bytes_per_day: float | None
    days_remaining: float | None
    exhaustion_date: datetime | None
    confidence: str
    sample_count: int


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp() / 86400


def calculate_forecast(samples: list[StorageSample], window_days: int) -> Forecast:
    if not samples:
        return Forecast(window_days, None, None, None, "Insufficient data", 0)
    ordered = sorted(samples, key=lambda x: x.timestamp)
    latest = ordered[-1]
    cutoff = latest.timestamp - timedelta(days=window_days)
    selected = [sample for sample in ordered if sample.timestamp >= cutoff]
    if len(selected) < 3 or (selected[-1].timestamp - selected[0].timestamp) < timedelta(days=2):
        return Forecast(window_days, None, None, None, "Insufficient data", len(selected))

    points = [(_timestamp(sample.timestamp), float(sample.used_bytes)) for sample in selected]
    slopes: list[float] = []
    for index, (x1, y1) in enumerate(points):
        for x2, y2 in points[index + 1 :]:
            if x2 > x1:
                slopes.append((y2 - y1) / (x2 - x1))
    rate = median(slopes) if slopes else 0.0
    if rate <= 0:
        return Forecast(window_days, rate, None, None, "Low", len(selected))

    remaining = max(0, latest.free_bytes)
    days_remaining = remaining / rate
    exhaustion = datetime.now(UTC) + timedelta(days=days_remaining)
    span = (selected[-1].timestamp - selected[0].timestamp).total_seconds() / 86400
    coverage = min(1.0, span / window_days)
    confidence = "High" if len(selected) >= 20 and coverage >= 0.8 else "Moderate"
    if len(selected) < 8 or coverage < 0.35:
        confidence = "Low"
    return Forecast(window_days, rate, days_remaining, exhaustion, confidence, len(selected))


def calculate_all(samples: list[StorageSample]) -> list[Forecast]:
    return [calculate_forecast(samples, days) for days in (7, 30, 90)]
