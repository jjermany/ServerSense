from dataclasses import dataclass
from enum import StrEnum


class ActionRisk(StrEnum):
    READ_ONLY = "read_only"
    STATE_CHANGE = "state_change"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class ActionRequest:
    principal: str
    action: str
    risk: ActionRisk
    confirmed_by_user: bool = False


class PermissionDenied(ValueError):
    pass


class ActionPolicy:
    """Central policy for current tools and future explicitly confirmed actions."""

    def authorize(self, request: ActionRequest) -> None:
        if request.principal == "sense" and request.risk is not ActionRisk.READ_ONLY:
            raise PermissionDenied("SENSE is restricted to read-only actions")
        if request.risk is not ActionRisk.READ_ONLY and not request.confirmed_by_user:
            raise PermissionDenied("State-changing actions require explicit user confirmation")


policy = ActionPolicy()
