import os
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from serversense.models import Setting


@dataclass(frozen=True)
class TimeZoneDetails:
    name: str
    source: str
    configurable: bool
    warning: str | None = None


def validate_time_zone(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 100:
        raise ValueError("Timezone must be a valid IANA name such as America/Chicago")
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Timezone must be a valid IANA name such as America/Chicago") from exc
    return normalized


def time_zone_details(db: Session) -> TimeZoneDetails:
    environment_name = os.environ.get("TZ", "").strip()
    if environment_name:
        try:
            return TimeZoneDetails(validate_time_zone(environment_name), "environment", False)
        except ValueError:
            return TimeZoneDetails(
                "UTC",
                "invalid_environment",
                False,
                f"Container TZ value {environment_name!r} is invalid; using UTC.",
            )

    general = db.get(Setting, "general")
    configured_name = str((general.value if general else {}).get("timezone", "")).strip()
    if configured_name:
        try:
            return TimeZoneDetails(validate_time_zone(configured_name), "settings", True)
        except ValueError:
            pass
    return TimeZoneDetails("UTC", "default", True)


def local_time(db: Session, value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.astimezone(ZoneInfo(time_zone_details(db).name))


def format_local_datetime(db: Session, value: datetime | None = None) -> str:
    localized = local_time(db, value)
    hour = localized.hour % 12 or 12
    suffix = "AM" if localized.hour < 12 else "PM"
    zone = localized.tzname() or time_zone_details(db).name
    return (
        f"{localized.strftime('%B')} {localized.day}, {localized.year} at "
        f"{hour}:{localized.minute:02d} {suffix} {zone}"
    )
