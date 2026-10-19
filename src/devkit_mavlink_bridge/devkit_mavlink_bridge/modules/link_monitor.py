"""Flight-controller heartbeat monitor.

Plain Python, no rclpy or pymavlink, so it can be unit tested without ROS.
Time comes from an injected monotonic clock, never from ROS time.
"""

import math
import time
from collections.abc import Callable

NEVER = 'never'
OK = 'ok'
LOST = 'lost'


class HeartbeatMonitor:
    """Track when the flight controller last sent a HEARTBEAT."""

    def __init__(self, timeout_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        """Treat the link as lost when no heartbeat arrived for more than timeout_s seconds.

        Raise ValueError if timeout_s is nonpositive or nonfinite.
        """
        if not math.isfinite(timeout_s) or timeout_s <= 0.0:
            raise ValueError(f'timeout_s must be a positive finite number, got {timeout_s!r}')
        self._timeout_s = timeout_s
        self._clock = clock
        self._stamp: float | None = None

    @property
    def timeout_s(self) -> float:
        """Maximum heartbeat age, in seconds, before the link counts as lost."""
        return self._timeout_s

    def beat(self) -> None:
        """Record a heartbeat received now."""
        self._stamp = self._clock()

    def state(self) -> str:
        """Return NEVER before the first heartbeat, then OK, or LOST once one is overdue."""
        if self._stamp is None:
            return NEVER
        return LOST if self._clock() - self._stamp > self._timeout_s else OK
