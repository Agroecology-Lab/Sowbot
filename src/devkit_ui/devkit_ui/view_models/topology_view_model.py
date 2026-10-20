from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.application_services.topology_application_service import (
        TopologyApplicationService,
    )
    from devkit_ui.models import TopoDoc, TopoNode


_LOG = logging.getLogger(__name__)


class TopologyViewModel:
    def __init__(self, app_service: TopologyApplicationService) -> None:
        """Initialize the topology view model.

        Parameters:
            app_service: TopologyApplicationService instance for operations.
        """
        self._app_service = app_service
        self.topo_doc: TopoDoc | None = None
        self.selected_node: str | None = None
        self.current_node: str = '—'
        self._map_archived_callbacks: list[Callable[[], None]] = []

        # Register this view model's callback with the domain via app service
        # This ensures doc changes sync automatically to the UI state
        self._app_service.register_doc_changed_callback(self._on_doc_changed)
        self._app_service.register_current_node_callback(self.set_current_node)

    def set_selected_node(self, name: str | None) -> None:
        """Set the selected node name, or clear the selection."""
        self.selected_node = name

    def set_current_node(self, name: str) -> None:
        """Update the node the robot is currently localised on."""
        self.current_node = name

    def _on_doc_changed(self, topo_doc: TopoDoc | None) -> None:
        """Callback invoked when topology document changes.

        Keeps the view model's doc mirror and UI state in sync with the domain.
        Called when:
        - Domain receives ROS updates from other services
        - Local mutations are published and echo back via subscription
        - Initial document is set during app initialization

        Parameters:
            topo_doc: Updated TopoDoc, or None if cleared.
        """
        self.update_doc(topo_doc)

    def update_doc(self, topo_doc: TopoDoc | None) -> None:
        """Replace the topology document and clear stale selection state.

        Called by _on_doc_changed callback when doc changes externally
        or locally via mutations.
        """
        self.topo_doc = topo_doc
        if self.selected_node is not None and (
            topo_doc is None or not topo_doc.has_node(self.selected_node)
        ):
            self.selected_node = None

    def add_node(self, node: TopoNode) -> None:
        """Add a node to the topology via the application service.

        Reverse edges are added to the referenced nodes before publishing. Mutations
        are not rolled back if adding edges or publishing fails; publisher errors propagate.

        Parameters:
            node: Node to add; all outgoing edge targets must already exist.

        Raises:
            ValueError: If no document is loaded or node already exists.
            KeyError: If an outgoing edge refers to a missing node.
        """
        self._app_service.add_node(node)

    def remove_node(self, name: str) -> None:
        """Remove a node from the topology via the application service.

        Parameters:
            name: Name of the node to remove.
        """
        self._app_service.remove_node(name)

    def has_node(self, name: str) -> bool:
        """Check if a node exists in the current topology.

        Parameters:
            name: Name of the node to check.

        Returns:
            True if node exists, False otherwise.
        """
        return self._app_service.has_node(name)

    def get_node(self, name: str) -> TopoNode | None:
        """Get a node by name from the current topology.

        Parameters:
            name: Name of the node to retrieve.

        Returns:
            TopoNode if found, None otherwise.
        """
        return self._app_service.get_node(name)

    @property
    def map_name(self) -> str:
        """Return the live map's name, or '?' when no map is loaded."""
        return self.topo_doc.name if self.topo_doc else '?'

    def register_map_archived_callback(self, callback: Callable[[], None]) -> None:
        """Call callback after the live map has been archived and cleared successfully."""
        self._map_archived_callbacks.append(callback)

    def save_map_as(self, name: str) -> str:
        """Save a named copy, preferring the saved map over the in-memory map.

        Return 'saved → <name>' or an 'ERROR: ...' status without replacing the live map.
        See TopologyApplicationService.save_map_as for name validation and overwrite rules.
        """
        return self._app_service.save_map_as(name)

    def archive_and_clear_map(self) -> str:
        """Archive the saved map as <name>_<N> and replace it with an empty default map.

        Return an archive status, or an 'ERROR:' status for a missing map or service exception.
        Completed writes and live-map changes are not rolled back on failure.
        Registered map-archived callbacks run only after the service returns successfully.
        Their exceptions propagate and prevent later callbacks from running.
        """
        if not self.topo_doc:
            return 'ERROR: no map loaded'
        try:
            archive = self._app_service.archive_and_clear()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOG.error('archive_and_clear_map failed: %s', exc)
            return f'ERROR: {exc}'
        for callback in self._map_archived_callbacks:
            callback()
        _LOG.info('archive_and_clear_map: archived to %s', archive)
        return f'archived → {archive}'
