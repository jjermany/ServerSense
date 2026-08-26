import threading
import time
from collections.abc import Callable


class ActiveViewerLease:
    """Process-local signal that an authenticated, visible UI is being viewed."""

    def __init__(
        self,
        ttl_seconds: float = 45,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._deadline = 0.0
        self._lock = threading.Lock()

    def renew(self) -> None:
        deadline = self._clock() + self._ttl_seconds
        with self._lock:
            self._deadline = max(self._deadline, deadline)

    def is_active(self) -> bool:
        with self._lock:
            return self._clock() < self._deadline


active_viewers = ActiveViewerLease()
