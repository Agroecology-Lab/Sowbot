"""Latest-message store with ages, for combining MAVLink messages that arrive separately.

Plain Python, no rclpy or pymavlink. Time comes from an injected monotonic clock, never
from the sender's boot time or ROS time.
"""

import time
from collections.abc import Callable


class LatestMessages:
    """Keep the most recent message of each kind and when it arrived."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start empty, reading arrival times from clock."""
        self._clock = clock
        self._items: dict[str, tuple[object, float]] = {}

    def put(self, kind: str, message: object) -> None:
        """Store message as the latest of its kind, stamped now."""
        self._items[kind] = (message, self._clock())

    def age_s(self, kind: str):
        """Return seconds since the last message of this kind, or None if none has arrived."""
        item = self._items.get(kind)
        return None if item is None else self._clock() - item[1]

    def fresh(self, kind: str, max_age_s: float):
        """Return the latest message of this kind if no older than max_age_s, else None."""
        item = self._items.get(kind)
        if item is None or self._clock() - item[1] > max_age_s:
            return None
        return item[0]

    def clear(self) -> None:
        """Forget every message, so nothing from before a link loss can be reused."""
        self._items.clear()
