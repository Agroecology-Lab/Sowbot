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
        """Return the number of the last node recorded; it continues from existing nodes."""
        return self._counter

    def start(self, prefix: str, interval: float, row_id: int | None, row_role: str,
              existing_names: Iterable[str], drop: DropFunction,
              on_recorded: RecordedCallback) -> bool:
        """Record one node at once, then one every interval seconds; return False if running.

        Names are prefix_N, numbered after the highest existing prefix_N. With a row_id the first
        node is an 'entry' and the rest are 'middle'; without one every node gets row_role.
        drop(name, row_id, role) records a node and on_recorded(name, number) follows each one.
        Both run on the timer's thread after the first. Errors from drop or the timer factory
        propagate; a failed start leaves nothing running.
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
        """Stop the timer and return the number of the last node recorded.

        Return 0 when nothing was recorded. Numbering restarts at the next start().
        """
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        count = self._counter
        self._counter = 0
        self._first = True
        return count
