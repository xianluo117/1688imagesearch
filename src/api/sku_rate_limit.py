"""Process-local pacing for SKU query starts on dedicated worker threads."""
import random
import threading
import time


class QueryStartLimiter:
    """Space actual starts without serializing the queries themselves."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_start: float | None = None

    def wait(self) -> None:
        # Only the start gate is serialized; network work runs outside this lock.
        with self._lock:
            if self._next_start is not None:
                remaining = self._next_start - time.monotonic()
                while remaining > 0:
                    time.sleep(remaining)
                    remaining = self._next_start - time.monotonic()
            # Use the actual release time, not an old schedule, to avoid bursts
            # after idle periods or delayed worker execution.
            self._next_start = time.monotonic() + random.uniform(2.0, 4.0)
