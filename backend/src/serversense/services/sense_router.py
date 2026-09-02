from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from serversense.services.tools import execute_tool

Intent = Literal[
    "telemetry",
    "status",
    "historical",
    "analysis",
    "troubleshooting",
    "recommendation",
    "general",
    "action",
]


@dataclass(frozen=True)
class RoutedRequest:
    intent: Intent
    direct: bool
    answer: str = ""
    tools: tuple[str, ...] = ()
    references: dict[str, Any] | None = None


def classify_intent(question: str) -> Intent:
    text = " ".join(question.lower().split())
    if any(
        phrase in text
        for phrase in (
            "restart ",
            "stop ",
            "start ",
            "delete ",
            "remove ",
            "run command",
            "execute ",
            "change setting",
            "move file",
        )
    ):
        return "action"
    if any(
        word in text
        for word in ("why", "explain", "cause", "correlat", "analy", "summarize", "summary")
    ):
        return "analysis"
    if any(word in text for word in ("fix", "troubleshoot", "diagnos", "what should i do")):
        return "troubleshooting"
    if any(word in text for word in ("recommend", "should i", "best way")):
        return "recommendation"
    if any(word in text for word in ("history", "historical", "changed", "trend", "over time")):
        return "historical"
    if any(word in text for word in ("status", "healthy", "running", "online", "started")):
        return "status"
    telemetry_terms = (
        "storage",
        "space",
        "capacity",
        "free",
        "disk",
        "drive",
        "temperature",
        "hot",
        "cpu",
        "memory",
        "ram",
        "uptime",
        "container",
        "docker",
        "array",
        "pool",
        "parity",
        "alert",
    )
    return "telemetry" if any(word in text for word in telemetry_terms) else "general"


def _bytes(value: int | float | None) -> str:
    if value is None:
        return "unavailable"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    amount = float(value)
    index = 0
    while amount >= 1000 and index < len(units) - 1:
        amount /= 1000
        index += 1
    return f"{amount:.2f} {units[index]}"


def _storage(db: Session, question: str) -> RoutedRequest:
    name = "get_array_capacity"
    storage = execute_tool(db, name, {}).get("storage")
    if not storage or storage.get("scope") != "combined_array_data_disks":
        answer = (
            "ServerSense does not currently have a normalized combined-array sample. "
            "It will not substitute a cache pool, root filesystem, or individual disk value."
        )
        return RoutedRequest("telemetry", True, answer, (name,), {"scope": "array"})
    total = int(storage["total_bytes"])
    free = int(storage["free_bytes"])
    used = int(storage["used_bytes"])
    percent = (used / total * 100) if total else 0
    text = question.lower()
    if "used" in text or "usage" in text or "full" in text:
        answer = (
            f"The combined array data disks are {percent:.1f}% used: {_bytes(used)} of "
            f"{_bytes(total)}, with {_bytes(free)} free. Named pools, parity, boot, and cache "
            "devices are excluded."
        )
    else:
        answer = (
            f"The combined array data disks have {_bytes(free)} free of {_bytes(total)} "
            f"({percent:.1f}% used). Named pools, parity, boot, and cache devices are excluded."
        )
    return RoutedRequest(
        "telemetry",
        True,
        answer,
        (name,),
        {"scope": "combined_array_data_disks", "sampled_at": storage.get("sampled_at")},
    )


def _storage_forecast(db: Session) -> RoutedRequest:
    name = "get_storage_forecast"
    result = execute_tool(db, name, {})
    current = result.get("current")
    forecast = next(
        (item for item in result.get("forecasts", []) if item.get("window_days") == 30), None
    )
    if not current or current.get("scope") != "combined_array_data_disks":
        answer = (
            "A combined-array forecast is unavailable until ServerSense receives a normalized "
            "array data-disk sample; no pool or individual disk value was substituted."
        )
    elif not forecast or forecast.get("days_remaining") is None:
        answer = "There is not yet enough measured combined-array history for a reliable forecast."
    else:
        answer = (
            f"ServerSense currently measures {_bytes(current['free_bytes'])} free across the "
            "combined array data disks. The deterministic 30-day trend projects about "
            f"{float(forecast['days_remaining']):.0f} days remaining. This is a projection, "
            "not a guarantee."
        )
    return RoutedRequest(
        "telemetry",
        True,
        answer,
        (name,),
        {
            "scope": "combined_array_data_disks",
            "sampled_at": current.get("sampled_at") if current else None,
        },
    )


def _disks(db: Session, question: str) -> RoutedRequest:
    name = "get_disk_smart_health"
    rows = execute_tool(db, name, {}).get("disks", [])
    text = question.lower()
    if not rows:
        return RoutedRequest("telemetry", True, "No current disk sample is available.", (name,))
    with_temperature = [row for row in rows if row.get("temperature_c") is not None]
    if "most free" in text or "free space" in text:
        measurable = [
            row
            for row in rows
            if row.get("total_bytes") is not None and row.get("used_bytes") is not None
        ]
        if not measurable:
            return RoutedRequest("telemetry", True, "Per-disk free space is unavailable.", (name,))
        freest = max(measurable, key=lambda row: int(row["total_bytes"]) - int(row["used_bytes"]))
        free = int(freest["total_bytes"]) - int(freest["used_bytes"])
        return RoutedRequest(
            "telemetry",
            True,
            f"{freest['name']} has the most measured free space at {_bytes(free)}. This is an individual physical-device value, not combined-array capacity.",
            (name,),
            {"entities": [{"type": "disk", "id": freest["id"], "name": freest["name"]}]},
        )
    if any(word in text for word in ("hot", "hottest", "temperature", "temp")):
        if not with_temperature:
            return RoutedRequest(
                "telemetry",
                True,
                "Disk temperatures are not available in the latest sample.",
                (name,),
            )
        hottest = max(with_temperature, key=lambda row: float(row["temperature_c"]))
        return RoutedRequest(
            "telemetry",
            True,
            f"The hottest measured disk is {hottest['name']} at {float(hottest['temperature_c']):.0f}°C.",
            (name,),
            {"entities": [{"type": "disk", "id": hottest["id"], "name": hottest["name"]}]},
        )
    unhealthy = [
        row
        for row in rows
        if str(row.get("smart_status", "unknown")).lower() not in {"healthy", "passed"}
    ]
    answer = (
        "All currently observed disks report healthy SMART status."
        if not unhealthy
        else f"{len(unhealthy)} disk(s) need attention: "
        + ", ".join(f"{row['name']} ({row.get('smart_status', 'unknown')})" for row in unhealthy)
        + "."
    )
    return RoutedRequest("status", True, answer, (name,), {"entities": []})


def _containers(db: Session, question: str) -> RoutedRequest:
    name = "get_container_status"
    rows = execute_tool(db, name, {}).get("containers", [])
    text = question.lower()
    named = [row for row in rows if str(row.get("name", "")).lower() in text]
    if named:
        row = named[0]
        health = f", health {row['health']}" if row.get("health") else ""
        answer = f"{row['name']} is {row.get('status', 'unknown')}{health}."
        refs = {"entities": [{"type": "container", "name": row["name"]}]}
        return RoutedRequest("status", True, answer, (name,), refs)
    bad = [
        row
        for row in rows
        if row.get("status") != "running" or row.get("health") not in (None, "healthy")
    ]
    answer = (
        f"All {len(rows)} observed containers are running and healthy."
        if rows and not bad
        else "No current container sample is available."
        if not rows
        else f"{len(bad)} of {len(rows)} containers need attention: "
        + ", ".join(f"{row['name']} ({row.get('status', 'unknown')})" for row in bad)
        + "."
    )
    return RoutedRequest("status", True, answer, (name,), {"entities": []})


def _resources(db: Session, question: str) -> RoutedRequest:
    name = "get_system_resources"
    values = execute_tool(db, name, {}).get("resources")
    if not values:
        return RoutedRequest(
            "telemetry", True, "No current system-resource sample is available.", (name,)
        )
    if "uptime" in question.lower() and values.get("uptime_seconds") is not None:
        seconds = int(values["uptime_seconds"])
        days, remainder = divmod(seconds, 86400)
        hours = remainder // 3600
        return RoutedRequest(
            "telemetry",
            True,
            f"Server uptime is {days} day{'s' if days != 1 else ''}, {hours} hour{'s' if hours != 1 else ''}.",
            (name,),
            {"scope": "system_resources"},
        )
    parts = []
    if values.get("cpu_percent") is not None:
        parts.append(f"CPU is {float(values['cpu_percent']):.1f}%")
    if values.get("memory_percent") is not None:
        parts.append(f"memory is {float(values['memory_percent']):.1f}%")
    answer = ", and ".join(parts) + "." if parts else "CPU and memory values are unavailable."
    return RoutedRequest("telemetry", True, answer, (name,), {"scope": "system_resources"})


def _pool(db: Session, question: str) -> RoutedRequest:
    name = "get_pool_status"
    pools = execute_tool(db, name, {}).get("pools", [])
    text = question.lower()
    matching = [row for row in pools if str(row.get("name", "")).lower() in text]
    row = matching[0] if matching else (pools[0] if len(pools) == 1 else None)
    if not row:
        return RoutedRequest(
            "telemetry", True, "No matching named-pool sample is available.", (name,)
        )
    answer = (
        f"The {row.get('name', 'selected')} pool has {_bytes(row.get('free_bytes'))} free of "
        f"{_bytes(row.get('total_bytes'))}. It is a named pool and is not included in combined-array capacity."
    )
    return RoutedRequest(
        "telemetry",
        True,
        answer,
        (name,),
        {"entities": [{"type": "pool", "name": row.get("name")}]},
    )


def _parity(db: Session, question: str) -> RoutedRequest:
    name = "get_array_status"
    result = execute_tool(db, name, {})
    state = result.get("platform_state", {})
    text = question.lower()
    if any(term in text for term in ("last", "finish", "completed", "when")):
        answer = "ServerSense does not currently collect the last parity-check completion time."
    else:
        action = state.get("parity_action", "unknown")
        errors = state.get("parity_errors")
        answer = f"The current parity action is {action}"
        if errors is not None:
            answer += f", with {errors} reported sync error{'s' if errors != 1 else ''}"
        answer += "."
    return RoutedRequest("status", True, answer, (name,), {"scope": "parity"})


def _alerts(db: Session) -> RoutedRequest:
    name = "get_recent_alerts"
    rows = execute_tool(db, name, {"limit": 20}).get("alerts", [])
    active = [row for row in rows if row.get("active")]
    if not active:
        answer = "There are no active, non-dismissed alerts."
    else:
        answer = (
            f"There are {len(active)} active alert{'s' if len(active) != 1 else ''}: "
            + "; ".join(str(row.get("title", "Untitled alert")) for row in active[:5])
        )
        answer += "."
    return RoutedRequest("status", True, answer, (name,), {"scope": "active_alerts"})


def route_request(db: Session, question: str) -> RoutedRequest:
    intent = classify_intent(question)
    text = question.lower()
    if intent == "action":
        return RoutedRequest(
            intent,
            True,
            "ServerSense and SENSE are read-only, so I can’t perform that action. I can inspect current telemetry and help you plan safe next steps.",
        )
    # Explanation, diagnosis, recommendations, history, and general conversation benefit from AI.
    if intent in {"analysis", "troubleshooting", "recommendation", "historical", "general"}:
        return RoutedRequest(intent, False)
    if any(phrase in text for phrase in ("run out", "how long", "forecast", "growth")):
        return _storage_forecast(db)
    if "parity" in text and not any(term in text for term in ("temperature", "temp", "hot")):
        return _parity(db, question)
    if "pool" in text or "cache" in text:
        return _pool(db, question)
    if "alert" in text:
        return _alerts(db)
    if any(word in text for word in ("storage", "space", "capacity", "free", "full")):
        return _storage(db, question)
    if any(word in text for word in ("disk", "drive", "smart", "hot", "temperature", "temp")):
        return _disks(db, question)
    if any(word in text for word in ("docker", "container", "plex")):
        return _containers(db, question)
    if any(word in text for word in ("cpu", "memory", "ram", "load", "uptime")):
        return _resources(db, question)
    overview = execute_tool(db, "get_server_overview", {})
    state = overview.get("array_status", "unknown")
    return RoutedRequest(
        intent,
        True,
        f"The array currently reports {state}.",
        ("get_server_overview",),
        {"scope": "array"},
    )
