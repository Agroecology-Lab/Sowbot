"""Result of starting, stopping or polling a tool process, shared by the process services."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolState:
    """What a tool call did or found.

    kind is one of 'started', 'already_running', 'running', 'stopped', 'exited', 'error', 'busy'
    and 'idle'. pid is set for started, already_running, running and exited; log is the tool's log
    path ('' if it has none); detail is the exception text for 'error', the return code for 'exited'
    and the RViz note for 'started'.
    """

    kind: str
    pid: int | None = None
    log: str = ''
    detail: str = ''
