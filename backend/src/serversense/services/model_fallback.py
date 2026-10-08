"""One bounded provider failover, with no expansion of SENSE permissions."""

from collections.abc import Callable
from time import monotonic
from typing import Any

import httpx

from serversense.services.codex import CodexError

FALLBACK_FIELDS = (
    "provider",
    "model",
    "endpoint",
    "context_window",
    "temperature",
    "timeout_seconds",
    "tool_calling",
)


def fallback_config(config: dict[str, Any]) -> dict[str, Any] | None:
    provider = config.get("fallback_provider", "disabled")
    model = config.get("fallback_model", "")
    if provider == "disabled" or not model or config.get("provider") == "disabled":
        return None
    result = dict(config)
    for key in FALLBACK_FIELDS:
        result[key] = config.get(f"fallback_{key}", config.get(key))
    result["api_key"] = config.get("fallback_api_key", "")
    result["fallback_provider"] = "disabled"
    if (config.get("curated_context") or {}).get("context_kind") == "broad_change_summary":
        result["tool_calling"] = "curated_context"
    return result


def eligible_failure(exc: Exception) -> bool:
    if isinstance(exc, CodexError):
        return exc.fallback_eligible
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {401, 403, 404, 408, 429, 500, 502, 503, 504}
    return isinstance(exc, (httpx.TransportError, TimeoutError))


def complete_with_fallback(
    config: dict[str, Any], request: Callable[[dict[str, Any]], Any]
) -> tuple[Any, dict[str, Any]]:
    """Background requests share one hard runtime and have no model tools."""
    started = monotonic()
    budget = float(config.get("max_runtime_seconds", 300))
    primary = config | {"max_runtime_seconds": budget}
    try:
        return request(primary), primary
    except Exception as exc:
        backup = fallback_config(config)
        remaining = budget - (monotonic() - started)
        if backup is None or not eligible_failure(exc) or remaining <= 0:
            raise
        backup["max_runtime_seconds"] = remaining
        backup["timeout_seconds"] = min(float(backup.get("timeout_seconds", 120)), remaining)
        return request(backup), backup
