"""Plain result types shared by the topology services (no ROS imports)."""
from collections.abc import Callable
from dataclasses import dataclass

from devkit_ui.models import TopoDoc, TopoNode


@dataclass(frozen=True)
class SwitchResult:
    """Outcome of asking the topological map manager to switch to a saved map."""
    available: bool = False
    success: bool = False
    message: str = ''


@dataclass(frozen=True)
class PersistResult:
    """How a persisted map change ended up.

    kind is one of:
        'live'           map saved and the map manager switched to it
        'no_srv'         map saved and published; the switch service is not available
        'switch_failed'  map saved and published after the switch failed or timed out
        'skipped'        nothing written (the node already exists on disk)
    """
    kind: str
    detail: str = ''

    def describe(self, success_msg: str) -> str:
        """Return the status text for a change reported as success_msg."""
        if self.kind == 'live':
            return f'{success_msg} — live'
        if self.kind == 'no_srv':
            return f'{success_msg} — live (no srv)'
        if self.kind == 'switch_failed':
            return f'{success_msg} (switch failed: {self.detail})'
        return success_msg


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of a row discovery start or stop request.

    active is None when the request did not change whether discovery is running.
    """
    active: bool | None
    status: str


@dataclass(frozen=True)
class NavUpdate:
    """Progress of a navigation goal, as reported by the navigation domain service.

    outcome is None while the goal is still in progress, otherwise one of
    'arrived', 'failed', 'rejected', 'cancelled' or 'unavailable'.
    """
    status: str
    navigating: bool
    outcome: str | None = None


class TopologyEditError(Exception):
    """A topology edit was rejected or had nothing to do.

    str(error) is the status text to show. warnings are log lines produced before the
    edit was rejected.
    """

    def __init__(self, status: str, warnings: tuple[str, ...] = ()) -> None:
        """Store the status text and any warnings to log."""
        super().__init__(status)
        self.warnings = warnings


@dataclass(frozen=True)
class StagedEdit:
    """A map edit already checked and ready to be persisted in the background.

    pending is the status to show while writing and success the status once saved.
    modify applies the same edit to the saved copy of the map. warnings and infos are
    log lines produced while staging. affected names the nodes the edit removes.
    """
    pending: str
    success: str
    modify: Callable[[TopoDoc], None]
    warnings: tuple[str, ...] = ()
    infos: tuple[str, ...] = ()
    affected: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StagedDrop:
    """A node already added to the live map, ready to be saved.

    pending is the status to show while writing. base prefixes the later statuses,
    short is the shorter prefix used when the map switch fails, and log is the line to
    log once the drop succeeds.
    """
    node: TopoNode
    pending: str
    base: str
    short: str
    log: str
