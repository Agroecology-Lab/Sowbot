from nicegui import ui

from devkit_ui.pages.delete_dialogs import confirm_delete_node
from devkit_ui.pages.obstacle_cards import attach_nav_card
from devkit_ui.pages.run.drop_node_card import DropNodeCard
from devkit_ui.pages.run.joystick_control_card import JoystickControlCard
from devkit_ui.pages.run.navigation_sidebar import NavigationSidebar
from devkit_ui.pages.run.node_map_card import NodeMapCard
from devkit_ui.pages.run.row_discovery_card import RowDiscoveryCard
from devkit_ui.pages.run.track_card import TrackCard
from devkit_ui.view_models.global_view_model import GlobalViewModel
from devkit_ui.view_models.map_authoring_view_model import MapAuthoringViewModel
from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel
from devkit_ui.view_models.run_view_model import RunViewModel
from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel
from devkit_ui.view_models.topology_view_model import TopologyViewModel


class NavPage(ui.column):
    """The Run tab: drive, map, record nodes, discover rows, mark obstacles and navigate."""

    def __init__(self, global_vm: GlobalViewModel, run_vm: RunViewModel,
                 topo_vm: TopologyViewModel, telemetry_vm: TelemetryViewModel,
                 obstacle_vm: ObstacleViewModel, authoring: MapAuthoringViewModel) -> None:
        """Compose the Run page and keep its displayed state in step with the robot.

        Parameters:
            global_vm: Soft e-stop state shared by the cards.
            run_vm: Joystick, track, drop-node, discovery and navigation state.
            topo_vm: The loaded map and the selected and current nodes.
            telemetry_vm: Odometry and GPS, refreshed on the page timer.
            obstacle_vm: The obstacle card's state.
            authoring: Drops, tracks and deletes nodes and sets the row action.
        """
        super().__init__()
        self.classes('w-full gap-0')
        self._global_vm = global_vm
        self._run_vm = run_vm
        self._topo_vm = topo_vm
        self._telemetry_vm = telemetry_vm
        self._prev: dict = {}

        with self:
            # A dropped connection never delivers the joystick's on_end, which would leave the
            # last cmd_vel in force. release_joystick only stops a joystick that still holds
            # a command.
            ui.context.client.on_disconnect(run_vm.release_joystick)

            with ui.row().classes('w-full gap-3 items-stretch'):
                with ui.column().classes('flex-1 gap-3').style('min-width:0'):
                    with ui.row().classes('w-full gap-3 items-stretch'):
                        JoystickControlCard(global_vm=global_vm, run_vm=run_vm)
                        NodeMapCard(topo_vm=topo_vm, pose_state=run_vm.node_map)

                    with ui.row().classes('w-full gap-3 items-start'):
                        TrackCard(state=run_vm.track,
                                  on_start=authoring.start_track, on_stop=authoring.stop_track)
                        DropNodeCard(state=run_vm.drop_node, topo_vm=topo_vm,
                                     on_drop=authoring.drop_node,
                                     on_row_action=authoring.set_row_action)
                        RowDiscoveryCard(state=run_vm.discovery,
                                         on_start=run_vm.start_discovery,
                                         on_stop=run_vm.stop_discovery)
                        # OBSTACLE: Mark Obstacle card next to Drop Node
                        attach_nav_card(obstacle_vm)

                self._sidebar = NavigationSidebar(
                    global_store=global_vm,
                    topo_vm=topo_vm,
                    nav_state=run_vm.topo,
                    on_go=self._go,
                    on_cancel=run_vm.cancel_navigation,
                    on_delete=lambda: confirm_delete_node(
                        topo_vm, topo_vm.selected_node, authoring.delete_node),
                    on_select=topo_vm.set_selected_node,
                )

            ui.on('topo_node_clicked', self._on_node_clicked)
            ui.timer(0.2, self._refresh)

    def _go(self) -> None:
        """Navigate to the selected node, if there is one."""
        target = self._topo_vm.selected_node
        if target:
            self._run_vm.go_to(target, self._global_vm.soft_estop_active)

    def _on_node_clicked(self, e) -> None:
        """Select the clicked topology node when it exists in the current map."""
        name = (e.args or {}).get('node')
        doc = self._topo_vm.topo_doc
        if name and doc and doc.has_node(name):
            self._topo_vm.set_selected_node(name)

    def _refresh(self) -> None:
        """Refresh the pose label, robot overlay and node list from the latest state."""
        self._telemetry_vm.refresh()
        self._run_vm.update_pose_label(self._telemetry_vm.odom, self._telemetry_vm.gps)

        topo_doc = self._topo_vm.topo_doc
        if topo_doc is None:
            return

        robot_pose = self._telemetry_vm.robot_pose()
        robot_key = None if robot_pose is None else (
            round(robot_pose[0], 1), round(robot_pose[1], 1), round(robot_pose[2], 2))
        self._run_vm.node_map.robot_pose = robot_pose

        snapshot = {
            'sel': self._topo_vm.selected_node,
            'cur': self._topo_vm.current_node,
            'stat': self._run_vm.topo.nav_status,
            'nav': self._run_vm.topo.navigating,
            'nodes': set(topo_doc.nodes),
            'robot': robot_key,
        }
        changed = {k for k, v in snapshot.items() if self._prev.get(k) != v}
        if not changed:
            return
        self._prev.update(snapshot)

        if changed & {'sel', 'nodes'}:
            self._sidebar.render_nodes(topo_doc.nodes, self._topo_vm.selected_node)
