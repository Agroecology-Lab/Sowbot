"""What the Plan tab needs to know about the Run tab's map, without depending on its view models."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.models import TopoDoc
    from devkit_ui.view_models.run_view_model import RunViewModel
    from devkit_ui.view_models.topology_view_model import TopologyViewModel


class MapContext:
    """Read-only view of the loaded map, the robot's node, the selection and the row action.

    The Plan view model takes this instead of the Run and Topology view models, so it knows
    four values rather than two whole view models. ui_node.py is the only place that joins them.
    Every property reads live state; nothing is cached.
    """

    def __init__(self, topo_vm: TopologyViewModel, run_vm: RunViewModel) -> None:
        """Bind to the view models that hold the map state and the row action."""
        self._topo_vm = topo_vm
        self._run_vm = run_vm

    @property
    def topo_doc(self) -> TopoDoc | None:
        """Return the loaded map, or None if there is none."""
        return self._topo_vm.topo_doc

    @property
    def current_node(self) -> str | None:
        """Return the node the robot is localised on; '—' or 'none' when it is on no node."""
        return self._topo_vm.current_node

    @property
    def selected_node(self) -> str | None:
        """Return the node picked in the map view, or None."""
        return self._topo_vm.selected_node

    @property
    def row_action(self) -> str:
        """Return the action new row edges use, as chosen on the Run tab."""
        return self._run_vm.drop_node.row_action
