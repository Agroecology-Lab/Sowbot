from nicegui import ui

from devkit_ui.constants import FULLSCREEN_MAP_PATH
from devkit_ui.utils.map_zoom import enable_zoom
from devkit_ui.utils.topo_renderer import build_robot_svg, build_svg, inject_click_js
from devkit_ui.view_models.run_view_model import RunViewModel
from devkit_ui.view_models.topology_view_model import TopologyViewModel


class NodeMapCard(ui.card):
    def __init__(self, topo_vm: TopologyViewModel,
                 pose_state: RunViewModel.NodeMap,
                 fullscreen: bool = False):
        """
        Initialize a card displaying the node map and robot marker overlay.

        Parameters:
            topo_vm (TopologyViewModel): Topology state providing the map,
                selected node, and current node.
            pose_state (RunViewModel.NodeMap): State providing the latest
                robot pose in map frame.
            fullscreen (bool): Fill the browser window and omit the button
                that opens this view, for the dedicated full-screen page.
        """
        super().__init__()

        self._topo_vm = topo_vm
        self._pose_state = pose_state

        # Change detection keys to avoid unnecessary DOM updates
        self._prev_map_key: tuple | None = None
        self._prev_robot_key: tuple | None = None

        self.classes('w-full' if fullscreen else 'flex-1')
        if fullscreen:
            self.style('height:100vh')

        with self:
            with ui.row().classes('w-full items-center justify-between no-wrap'):
                ui.label('Node Map').classes('sec-label')
                if not fullscreen:
                    # NOTE: a real link target keeps the click a user gesture, so popup blockers allow the new tab.
                    ui.button(icon='open_in_full').props(
                        f'flat dense round href={FULLSCREEN_MAP_PATH} target=_blank '
                        'title="Open full screen in a new tab"')

            # Node map + robot marker overlay (same viewBox, so they
            # align). Overlay is pointer-events:none so clicks reach
            # the nodes; it updates on movement without rebuilding
            # the clickable node DOM.
            if fullscreen:
                self._build_zoomable_map()
            else:
                with ui.element('div').classes('relative w-full'):
                    self._map_html = ui.html().classes('w-full')
                    self._robot_html = ui.html().classes(
                        'absolute top-0 left-0 w-full'
                    ).style('pointer-events:none')

        inject_click_js()
        ui.timer(0.2, self._refresh)

    def _build_zoomable_map(self) -> None:
        """Build both layers inside a clipped, pannable and zoomable box.

        Both layers fill the same box, so the SVGs letterbox identically and stay aligned.
        """
        layer = 'absolute inset-0 w-full h-full [&_svg]:w-full [&_svg]:h-full'
        with ui.element('div').classes('relative w-full flex-1 overflow-hidden') as viewport:
            with ui.element('div').classes('absolute inset-0') as content:
                self._map_html = ui.html().classes(layer)
                self._robot_html = ui.html().classes(layer).style('pointer-events:none')
        # NOTE: NiceGUI renders each element with the DOM id c<element id>.
        enable_zoom(f'c{viewport.id}', f'c{content.id}')

    def _refresh(self) -> None:
        """Rebuild and display the map and robot SVG overlays."""
        doc = self._topo_vm.topo_doc
        nodes = doc.nodes if doc else []
        selected = self._topo_vm.selected_node
        current = self._topo_vm.current_node
        pose = self._pose_state.robot_pose

        # Only rebuild the heavy clickable map when topology, selection, or current node changes
        # Key includes: document identity + node count + node names + selection + current
        map_key = (id(doc), len(nodes), tuple(n.name for n in nodes), selected, current)
        if map_key != self._prev_map_key:
            self._prev_map_key = map_key
            self._map_html.set_content(
                build_svg(
                    doc,
                    selected,
                    current,
                )
            )

        # Only rebuild the robot marker overlay when pose or node bounds change
        # Quantize pose to 0.1m/0.01rad to avoid thrashing on tiny float diffs
        robot_pose_key = (
            None if pose is None
            else (round(pose[0], 1), round(pose[1], 1), round(pose[2], 2))
        )
        robot_key = (id(doc), len(nodes), robot_pose_key)
        if robot_key != self._prev_robot_key:
            self._prev_robot_key = robot_key
            self._robot_html.set_content(
                build_robot_svg(nodes, pose)
            )
