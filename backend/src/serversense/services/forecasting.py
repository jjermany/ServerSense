from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from statistics import median

from serversense.models import StorageSample

MAX_SLOPE_SAMPLES = 256


@lru_cache(maxsize=12)
def _robust_rate(points: tuple[tuple[float, float, int], ...]) -> float:
    # Evenly cover the selected window, retaining both endpoints. This bounds
    # the quadratic estimator at 32,640 slopes, even with five-minute samples.
    if len(points) > MAX_SLOPE_SAMPLES:
        points = tuple(
            points[index * (len(points) - 1) // (MAX_SLOPE_SAMPLES - 1)]
            for index in range(MAX_SLOPE_SAMPLES)
        )
    slopes = [
        (y2 - y1) / (x2 - x1)
        for index, (x1, y1, segment1) in enumerate(points)
        for x2, y2, segment2 in points[index + 1 :]
        if x2 > x1 and segment1 == segment2
    ]
    return median(slopes) if slopes else 0.0


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

    # Separate substantial cleanup steps from sustained consumption. Detect
    # them before downsampling so even a five-minute deletion is retained.
    # The relative floor avoids treating small routine fluctuations as resets;
    # the typical-change floor preserves sustained declining trends.
    changes = [
        current.used_bytes - previous.used_bytes
        for previous, current in zip(selected, selected[1:], strict=False)
    ]
    typical_change = median(abs(change) for change in changes)
    segment = 0
    segmented_points: list[tuple[float, float, int]] = []
    for index, sample in enumerate(selected):
        if index:
            drop = -changes[index - 1]
            threshold = max(sample.total_bytes * 0.005, typical_change * 8)
            # A rebound indicates a transient bad reading, not a cleanup.
            rebound = changes[index] if index < len(changes) else 0
            if drop > threshold and rebound < drop * 0.8:
                segment += 1
        segmented_points.append((_timestamp(sample.timestamp), float(sample.used_bytes), segment))
    points = tuple(segmented_points)
    rate = _robust_rate(points)
    if rate <= 0:
        return Forecast(window_days, rate, None, None, "Low", len(selected))

    remaining = max(0, latest.free_bytes)
    days_remaining = remaining / rate
    try:
        exhaustion = datetime.now(UTC) + timedelta(days=days_remaining)
    except OverflowError:
        exhaustion = None
    span = (selected[-1].timestamp - selected[0].timestamp).total_seconds() / 86400
    coverage = min(1.0, span / window_days)
    confidence = "High" if len(selected) >= 20 and coverage >= 0.8 else "Moderate"
    if len(selected) < 8 or coverage < 0.35:
        confidence = "Low"
    return Forecast(window_days, rate, days_remaining, exhaustion, confidence, len(selected))


def calculate_all(samples: list[StorageSample]) -> list[Forecast]:
    return [calculate_forecast(samples, days) for days in (7, 30, 90)]
