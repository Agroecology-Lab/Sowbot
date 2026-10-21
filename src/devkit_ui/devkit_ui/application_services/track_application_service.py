"""Use case for track mode: record a topology node now, then on a timer, numbering as it goes.

This owns the timer, the node counter and the entry/middle role sequence. What a recorded node
is, and where it is saved, stays with the caller's drop function. No ROS or NiceGUI imports.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol


class Cancellable(Protocol):
    """A repeating timer that can be stopped."""

    def cancel(self) -> None:
        """Stop the timer."""


TimerFactory = Callable[[float, Callable[[], None]], Cancellable]
DropFunction = Callable[[str, 'int | None', str], None]
RecordedCallback = Callable[[str, int], None]


class TrackApplicationService:
    """Record nodes at the robot's position at a fixed interval until stopped."""

    def __init__(self, create_timer: TimerFactory) -> None:
        """Keep the factory that makes the repeating timer; nothing runs until start()."""
        self._create_timer = create_timer
        self._timer: Cancellable | None = None
        self._counter = 0
        self._first = True

    @property
    def running(self) -> bool:
        """Return whether a track is being recorded."""
        return self._timer is not None

    @property
    def count(self) -> int:
        """Return the last attempted node number, including numbering continued from existing nodes.

        The number advances before drop is called, even if drop or on_recorded raises.
        Return 0 before starting or after a successful stop.
        """
        return self._counter

    def start(self, prefix: str, interval: float, row_id: int | None, row_role: str,
              existing_names: Iterable[str], drop: DropFunction,
              on_recorded: RecordedCallback) -> bool:
        """Record one node at once, then one every interval seconds; return True on startup.

        Return False without changes if already running. Names are prefix_N, starting at 1 or
        after the highest existing numeric suffix. With a row_id (including 0) the first node
        is an 'entry' and the rest are 'middle'; without one every node gets row_role.
        drop(name, row_id, role) records a node and on_recorded(name, number) follows each one.
        The first pair runs synchronously; later calls run in the timer's callback context.
        interval is passed to the timer factory without validation.
        Errors from either callback or the timer factory propagate to their caller. A failed
        initial callback creates no timer; a factory failure does not undo the initial drop.
        Neither failure resets the counter.
        """
        if self._timer is not None:
            return False
        head = prefix + '_'
        numbers = [int(n[len(head):]) for n in existing_names
                   if n.startswith(head) and n[len(head):].isdigit()]
        self._counter = max(numbers, default=0)
        self._first = True

        def record() -> None:
            """Record the next node in the sequence."""
            self._counter += 1
            name = f'{prefix}_{self._counter}'
            if row_id is not None:
                role = 'entry' if self._first else 'middle'
                self._first = False
            else:
                role = row_role
            drop(name, row_id, role)
            on_recorded(name, self._counter)

        record()
        self._timer = self._create_timer(interval, record)
        return True

    def stop(self) -> int:
        """Stop the timer and return the last attempted node number, including existing numbering.

        Reset the counter to 0; the next start derives numbering from existing_names again.
        Return 0 before starting or after a previous successful stop. Timer cancellation errors
        propagate without clearing the timer or resetting the counter.
        """
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        count = self._counter
        self._counter = 0
        self._first = True
        return count
