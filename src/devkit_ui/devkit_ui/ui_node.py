# pylint: disable=duplicate-code,too-many-lines,consider-using-with
"""
ui_node.py — Sowbot web cockpit on :80
"""

import re
import threading
import traceback
from collections.abc import Callable
from importlib import resources

# The following imports get generated in the Dockerfile, they aren't available to pylint
# pylint: disable=import-error
import rclpy
from nav_msgs.msg import Odometry
from nicegui import app, ui, ui_run
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import NavSatFix
from std_msgs.msg import String

# MISSION: store owns missions.yaml, scheduling, and run recording.
from devkit_ui.actions import ACTIONS
from devkit_ui.application_services.drive_application_service import DriveApplicationService
from devkit_ui.application_services.mission_application_service import MissionApplicationService
from devkit_ui.application_services.navigation_application_service import (
    NavigationApplicationService,
)
from devkit_ui.application_services.plan_application_service import PlanApplicationService
from devkit_ui.application_services.robot_brain_application_service import RobotBrainApplicationService
from devkit_ui.application_services.row_discovery_application_service import (
    RowDiscoveryApplicationService,
)
from devkit_ui.application_services.sim_asset_service import SimAssetService
from devkit_ui.application_services.sim_world_service import SimWorldService
from devkit_ui.application_services.telemetry_application_service import (
    TelemetryApplicationService,
)
from devkit_ui.application_services.topology_application_service import (
    TopologyApplicationService,
)
from devkit_ui.application_services.topology_edit_service import TopologyEditService
from devkit_ui.constants import FIELD27_CENTER, NODE_NAME, ROW_ACTION, VISION_ROW_ACTION
from devkit_ui.domain_services.drive_domain_service import DriveDomainService
from devkit_ui.domain_services.mission_domain_service import (
    ERROR,
    INFO,
    NAV_TIMEOUT_SEC,
    OK,
    PROGRESS,
    WARN,
    MissionDomainService,
    MissionError,
    resolve_steps,
)
from devkit_ui.domain_services.navigation_domain_service import NavigationDomainService
from devkit_ui.domain_services.robot_brain_domain_service import RobotBrainDomainService
from devkit_ui.domain_services.row_discovery_domain_service import RowDiscoveryDomainService
from devkit_ui.domain_services.telemetry_domain_service import TelemetryDomainService
from devkit_ui.domain_services.topology_domain_service import TopologyDomainService
from devkit_ui.missions import MissionStore
from devkit_ui.models import (
    TopoDoc,
    TopoNode,
)

# pylint: enable=import-error
# OBSTACLE: obstacle manager, view model and card helpers
from devkit_ui.obstacles import ObstacleManager
from devkit_ui.pages.obstacle_cards import (
    attach_mission_obstacle_panel,
    attach_nav_card,
)
from devkit_ui.pages.plan.boundary_map_card import BoundaryMapCard
from devkit_ui.pages.plan.plan_controls_card import PlanControlsCard
from devkit_ui.pages.run.drop_node_card import DropNodeCard
from devkit_ui.pages.run.joystick_control_card import JoystickControlCard
from devkit_ui.pages.run.navigation_sidebar import NavigationSidebar
from devkit_ui.pages.run.node_map_card import NodeMapCard
from devkit_ui.pages.run.row_discovery_card import RowDiscoveryCard
from devkit_ui.pages.run.track_card import TrackCard
from devkit_ui.pages.sim.sim_page import SimPage
from devkit_ui.pages.system.system_page import SystemPage
from devkit_ui.process_registry import shutdown_tools
from devkit_ui.ros_gateway import RosGateway
from devkit_ui.topo_results import TopologyEditError
from devkit_ui.view_models.global_view_model import GlobalViewModel
from devkit_ui.view_models.mission_view_model import MissionViewModel
from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel
from devkit_ui.view_models.plan_view_model import PlanViewModel
from devkit_ui.view_models.robot_brain_view_model import RobotBrainViewModel
from devkit_ui.view_models.run_view_model import RunViewModel
from devkit_ui.view_models.sim_view_model import SimViewModel
from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel
from devkit_ui.view_models.topology_view_model import TopologyViewModel

_NAME_RE = re.compile(r'^[A-Z0-9_]+$')


# ── Import CSS ────────────────────────────────────────────────────────────────

def load_css() -> str:
    """Return the bundled app stylesheet from the package resources."""
    try:
        return resources.files('devkit_ui').joinpath('css/app.css').read_text(encoding='utf-8')
    except FileNotFoundError:
        return ''


_APP_CSS = load_css()

app.on_shutdown(shutdown_tools)

# ── ROS node ──────────────────────────────────────────────────────────────────

MISSION_STATUS_COLOURS = {
    INFO: '#57606a', PROGRESS: '#0969da', WARN: '#9a6700', ERROR: '#cf222e', OK: '#1a7f37'}


class NiceGuiNode(Node):

    def __init__(self) -> None:
        """
        Initialize the ROS node, GUI view models, navigation interfaces, sensor state, and
        mission-planning components.

        In simulation, configure the dedicated fallback GPS source used when no recent real GPS fix
        is available. Register the NiceGUI root page and initialize topology, obstacle, mission,
        safety, and navigation state.
        """
        super().__init__(NODE_NAME)

        # Instantiate the temporary ROS gateway bridge to abstract underlying ROS nodes
        self._ros = RosGateway(self)
        self._ros.forward_python_logging('devkit_ui')

        # The sim flag is the authoritative signal, plumbed from manage.py's is_sim through
        # devkit.launch.py -> ui.launch.py.
        self.declare_parameter('sim', False)
        self._is_sim = bool(self.get_parameter('sim').value)

        # Initialize the domain services using the gateway.
        self._drive_domain_service = DriveDomainService(self._ros)
        self._topo_domain_service = TopologyDomainService(self._ros)
        self._robot_brain_domain_service = RobotBrainDomainService(self._ros)
        self._row_discovery_domain_service = RowDiscoveryDomainService(self._ros)
        self._nav_domain_service = NavigationDomainService(self._ros)
        # FIELD27_CENTER is the datum the sim GPS shim publishes. See FIELD27_CENTER for why a
        # mismatch with the field's real datum is dangerous, not just cosmetic.
        self._telemetry_domain_service = TelemetryDomainService(
            self._ros, is_sim=self._is_sim, fake_gps_datum=FIELD27_CENTER)

        # Set up the application service layers on top of domain services.
        self._drive_app_service = DriveApplicationService(
            self._drive_domain_service)
        self._topo_app_service = TopologyApplicationService(
            self._topo_domain_service
        )
        self._topo_edit_service = TopologyEditService(self._topo_app_service)
        self._robot_brain_app_service = RobotBrainApplicationService(self._robot_brain_domain_service)
        self._row_discovery_app_service = RowDiscoveryApplicationService(
            self._row_discovery_domain_service)
        self._nav_app_service = NavigationApplicationService(self._nav_domain_service)
        self._telemetry_app_service = TelemetryApplicationService(
            self._telemetry_domain_service)
        self._plan_app_service = PlanApplicationService(
            self._topo_app_service, self._topo_edit_service, self._telemetry_app_service,
            is_sim=self._is_sim)

        # Initialize view models
        self._global_vm = GlobalViewModel(self._drive_app_service)
        self._run_vm = RunViewModel(
            self._drive_app_service, self._row_discovery_app_service, self._nav_app_service)
        self._topo_vm = TopologyViewModel(self._topo_app_service)
        self._topo_vm.register_map_archived_callback(self._on_map_archived)
        self._plan_vm = PlanViewModel(self._plan_app_service, self._topo_vm, self._run_vm)
        self._telemetry_vm = TelemetryViewModel(self._telemetry_app_service)
        self._robot_brain_vm = RobotBrainViewModel(self._robot_brain_app_service)
        sim_assets = SimAssetService()
        self._sim_vm = SimViewModel(sim_assets, SimWorldService(sim_assets))

        # Initialize with default demo document
        self._topo_app_service.initialize_with_default()

        _SENSOR_QOS = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self._ros.create_subscription(
            String,
            '/current_node',
            lambda m: self._topo_vm.set_current_node(m.data),
            _SENSOR_QOS,
        )

        # Per-session, never persisted: each new map starts on geometry-only rows.
        self._row_action: str = ROW_ACTION

        self._track_timer:   object  | None   = None
        self._track_counter: int              = 0
        self._track_first:   bool             = True

        # OBSTACLE: manager owns obstacles.yaml + /obstacles publisher; the view model is what
        # the cards bind to. Start after the telemetry service exists so the getters resolve.
        self._obstacle_mgr = ObstacleManager(
            self._ros, lambda: self.latest_odom, lambda: self.latest_gps)
        self._obstacle_vm = ObstacleViewModel(self._obstacle_mgr, lambda: self.latest_gps)
        self._obstacle_mgr.start()

        # MISSION: store owns missions.yaml, scheduling, and run recording.
        self._mission_store = MissionStore()
        self._mission_store.attach(self._ros)

        self._mission_service = MissionDomainService(self._ros)
        self._mission_vm = MissionViewModel(MissionApplicationService(
            self._mission_store, self._mission_service, self._topo_app_service.get_doc))

        @ui.page('/')
        def page():
            """
            Builds the NiceGUI application content.
            """
            self.content()

    # ── Topology document property (bridge to view model) ──────────────────
    # All existing code references self._topo_doc; this property transparently
    # gets the mirrored doc from the view model, which is kept in sync with
    # the domain service via the callback. Allows gradual refactoring without
    # changing all call sites at once.

    @property
    def _topo_doc(self) -> TopoDoc | None:
        """Get the current topology document (view model's mirror of domain)."""
        return self._topo_vm.topo_doc

    # ── Telemetry (bridge to the telemetry service) ────────────────────────
    # ObstacleManager, MissionStore and the F2C/drop-node code read the latest odometry and GPS
    # fix off the node. These read-only properties keep those call sites unchanged while the
    # state itself lives in the telemetry service.

    @property
    def latest_odom(self) -> Odometry | None:
        """Return the odometry currently driving the UI, or None before any has arrived."""
        return self._telemetry_app_service.latest_odom

    @property
    def latest_gps(self) -> NavSatFix | None:
        """Return the cached GNSS message, including invalid fixes, or None before reception.

        In simulation this may be a fallback fix when no valid real fix is recent.
        """
        return self._telemetry_app_service.latest_gps

    # ── nav actions ───────────────────────────────────────────────────────────

    def send_nav_goal(self, target: str) -> None:
        """Send a navigation goal to the specified topology node.

        Parameters:
            target (str): Name of the topology node to navigate to.
        """
        if self._run_vm.topo.navigating or self._global_vm.soft_estop_active:
            self.get_logger().warn(
                'send_nav_goal: rejected — navigation already in progress '
                'or soft-estop active')
            return
        self._run_vm.navigate_to(target)

    def cancel_nav_goal(self) -> None:
        """Cancel the active navigation goal."""
        self._run_vm.cancel_navigation()

    # ── node dropping ─────────────────────────────────────────────────────────

    def drop_topo_node(self, name: str, row_id: int | None,
                       row_role: str = 'entry') -> None:
        """Add a topology node at the current robot position and persist it to the active map.

        Add the node in memory immediately, then save and reload in a background thread.
        Use (0, 0) in simulation if odometry is unavailable. Connect to the current node,
        or the selected node as a fallback; save the outgoing edge only if its target is
        already on disk. Row nodes use the selected row action. Validation failures and
        worker exceptions set drop_node.status; a failed save does not roll back the
        initial in-memory addition. The rules live in TopologyEditService.

        Parameters:
            name (str): Name, stripped and uppercased, with spaces replaced by underscores
                and characters outside A-Z, 0-9, and underscore removed.
            row_id (int | None): Row identifier to associate with the node, or None for a
                navigation node.
            row_role (str): Role of the node within its row, such as "entry" or "exit".
        """
        odom = self.latest_odom
        try:
            drop = self._topo_edit_service.stage_drop(
                name, row_id, row_role,
                pose=(odom.pose.pose.position.x, odom.pose.pose.position.y) if odom else None,
                is_sim=self._is_sim, gps=self.latest_gps, row_action=self._row_action,
                current_node=self._topo_vm.current_node,
                selected_node=self._topo_vm.selected_node)
        except TopologyEditError as e:
            self._run_vm.drop_node.status = str(e)
            return
        self._run_vm.drop_node.status = drop.pending
        node_name = drop.node.name

        def _publish_and_persist():
            """Save the dropped node and make the updated map available to navigation.

            The service creates a missing map from defaults, saves only edges to nodes
            already on disk and adds no reverse edges. A duplicate name on disk skips
            writing. Worker exceptions become drop_node.status errors.
            """
            try:
                def _saved() -> None:
                    """Show reloading status after saving the node and before switching maps."""
                    self._run_vm.drop_node.status = f'{drop.base} — reloading…'

                result = self._topo_app_service.drop_node(drop.node, on_saved=_saved)
                if result.kind == 'skipped':
                    self.get_logger().warn(f'Node {node_name} already in file — skipping write')
                    return
                if result.kind == 'switch_failed':
                    self._run_vm.drop_node.status = (
                        f'{drop.short} saved (switch failed: {result.detail})')
                    self.get_logger().warn(
                        f'switch_topological_map failed ({result.detail})')
                else:
                    self._run_vm.drop_node.status = result.describe(drop.base)
                self.get_logger().info(drop.log)
            except Exception as e:
                self._run_vm.drop_node.status = f'ERROR: {e}'
                self.get_logger().error(
                    f'drop_topo_node failed: {e} ({type(e)}\n{traceback.format_exc()})')

        threading.Thread(target=_publish_and_persist, daemon=True).start()

    # ── track mode ────────────────────────────────────────────────────────────

    def start_track(self, prefix: str, interval: float,
                    row_id: int | None, row_role: str | None) -> None:
        """Record a topology node immediately, then periodically using the node's clock.

        Parameters:
            prefix (str): Prefix used for numbered node names after normalization.
            interval (float): Time in seconds between recorded nodes.
            row_id (int | None): Optional row identifier; its first node is an entry,
                and subsequent nodes are middle nodes.
            row_role (str | None): Role assigned when no row identifier is provided.

        Continue numbering after existing nodes with the same prefix.
        Report an empty normalized prefix or an existing timer in tracking status and return.
        Requires a loaded topology document; timer creation errors propagate after the
        initial recording attempt.
        """
        prefix = re.sub(r'[^A-Z0-9_]', '', prefix.strip().upper().replace(' ', '_'))
        if not prefix:
            self._run_vm.track.running = False
            self._run_vm.track.status = 'ERROR: prefix required'
            return
        if self._track_timer is not None:
            self._run_vm.track.running = True
            self._run_vm.track.status = 'ERROR: already running'
            return
        existing = [n.name for n in self._topo_doc.nodes
                    if n.name.startswith(prefix + '_') and n.name[len(prefix)+1:].isdigit()]
        self._track_counter = (max(int(n[len(prefix)+1:]) for n in existing)
                               if existing else 0)
        self._track_first  = True

        self._run_vm.track.prefix = prefix
        self._run_vm.track.interval = interval
        self._run_vm.track.row_id = row_id
        self._run_vm.track.row_role = row_role or 'entry'
        self._run_vm.track.running = True
        self._run_vm.track.status = ''

        is_row = row_id is not None

        def _drop() -> None:
            """Record the next topology node in the active tracking sequence and update tracking
            status.
            """
            self._track_counter += 1
            node_name = f'{prefix}_{self._track_counter}'
            if is_row:
                role = 'entry' if self._track_first else 'middle'
                self._track_first = False
            else:
                role = row_role
            self.drop_topo_node(node_name, row_id, role)
            self._run_vm.track.status = f'recording  {node_name}  (#{self._track_counter})'

        _drop()
        self._track_timer = self._ros.create_timer(interval, _drop)

    def stop_track(self) -> None:
        """Stop tracking and mark the last tracked node as the row exit when applicable."""
        if self._track_timer is not None:
            self._track_timer.cancel()
            self._track_timer = None
        is_row = self._run_vm.track.row_id is not None
        if is_row and self._track_counter > 0:
            last_name = f'{self._run_vm.track.prefix}_{self._track_counter}'
            self._patch_node_role(last_name, 'exit')
            self._run_vm.track.status = (f'stopped — {last_name} marked exit'
                                      f'  (#{self._track_counter} nodes)')
        else:
            self._run_vm.track.status = f'stopped at #{self._track_counter}'
        self._run_vm.track.running = False
        self._track_counter = 0
        self._track_first = True
        self._run_vm.track.prefix = ''
        self._run_vm.track.row_id = None
        self._run_vm.track.row_role = 'entry'

    # ── shared topo-map persistence helper ────────────────────────────────────

    # ── Row discovery ────────────────────────────────────────────────────────

    def start_discovery(self) -> None:
        """Start row discovery."""
        self._run_vm.start_discovery()

    def stop_discovery(self) -> None:
        """Stop row discovery."""
        self._run_vm.stop_discovery()

    def _persist_and_reload(self, modify_fn: Callable[[TopoDoc], None], status_owner: object,
                             status_attr: str, success_msg: str) -> None:
        """
                             Apply a topology modification, persist the updated map, and make it
                             live in a background thread. Requires a loaded topology document.
                             Worker exceptions become status messages; returning does not mean
                             the map has been saved or reloaded.

                             Parameters:
                                 modify_fn (Callable[[TopoDoc], None]): Function that mutates the
                                 topology document.
                                 status_owner (object): Object whose status attribute receives
                                 progress or error messages.
                                 status_attr (str): Name of the status attribute to update.
                                 success_msg (str): Message reported after the map is persisted
                                 successfully.
                             """
        def _work():
            """Run the service's persist-and-reload and report the outcome as a status."""
            try:
                result = self._topo_app_service.persist_and_reload(modify_fn)
                setattr(status_owner, status_attr, result.describe(success_msg))
                self.get_logger().info(f'_persist_and_reload: {success_msg}')
            except Exception as e:
                setattr(status_owner, status_attr, f'ERROR: {e}')
                self.get_logger().error(
                    f'_persist_and_reload failed: {e} ({type(e)}\n{traceback.format_exc()})')

        threading.Thread(target=_work, daemon=True).start()

    # ── F2C → topo rows ──────────────────────────────────────────────────────

    # ── Delete topo nodes / rows ─────────────────────────────────────────────

    def delete_topo_node(self, name: str) -> None:
        """Delete a topology node and persist the updated map.

        Parameters:
            name (str): Name of the topology node to delete.
        """
        self._persist_deletion(self._topo_edit_service.stage_delete_node, name)

    def delete_row(self, row_id: int) -> None:
        """Delete all topology nodes belonging to a row and persist the updated map.

        Parameters:
            row_id (int): Identifier of the row whose nodes should be deleted.
        """
        self._persist_deletion(self._topo_edit_service.stage_delete_row, row_id)

    def _persist_deletion(self, stage, target) -> None:
        """Stage a deletion, clear a selection it removes, and persist it in the background.

        Parameters:
            stage: TopologyEditService method that returns the StagedEdit for target.
            target: Node name or row id passed to stage.
        """
        status_owner = self._run_vm.topo
        try:
            edit = stage(target)
        except TopologyEditError as e:
            status_owner.delete_status = str(e)
            return
        if self._topo_vm.selected_node in edit.affected:
            self._topo_vm.set_selected_node(None)
        status_owner.delete_status = edit.pending
        self._persist_and_reload(edit.modify, status_owner, 'delete_status', edit.success)

    def _log_edit_lines(self, warnings: tuple[str, ...], infos: tuple[str, ...]) -> None:
        """Log the warning and info lines produced while staging a topology edit."""
        for line in infos:
            self.get_logger().info(line)
        for line in warnings:
            self.get_logger().warn(line)

    # ── Confirmation dialogs ─────────────────────────────────────────────────

    async def confirm_delete_node(self, name: str | None) -> None:
        """Ask for confirmation before deleting and persisting a topology node.

        With a loaded map, ignore an empty name or a node absent from that map.
        Canceling the dialog leaves the node unchanged.
        """
        if not name or not self._topo_doc.has_node(name):
            return
        nd = self._topo_doc.get_node(name)
        rid = nd.meta.get('row_id')
        with ui.dialog() as d, ui.card():
            ui.label(f'Delete topo node "{name}"?').classes('font-semibold')
            if rid is not None:
                ui.label(
                    f'This is part of row {rid}. To delete the whole row '
                    f'(entry + exit), use the ✕ on the Plan tab instead.'
                ).classes('text-xs').style('color:#9a6700;max-width:340px')
            ui.label('This persists immediately and cannot be undone.').classes(
                'text-xs').style('color:#8c959f')
            with ui.row().classes('w-full justify-end gap-2 mt-2'):
                ui.button('Cancel', on_click=lambda: d.submit('cancel')).props('flat no-caps')
                ui.button('Delete', color='negative',
                          on_click=lambda: d.submit('ok')).props('no-caps')
        if await d == 'ok':
            self.delete_topo_node(name)

    async def confirm_delete_row(self, row_id: int) -> None:
        targets = sorted(node.name for node in self._topo_doc.nodes
                         if node.meta.get('row_id') == row_id)
        if not targets:
            return
        with ui.dialog() as d, ui.card():
            ui.label(f'Delete row {row_id}?').classes('font-semibold')
            ui.label(f'{len(targets)} nodes will be removed:').classes('text-xs').style(
                'color:#57606a')
            ui.label(', '.join(targets)).classes('text-xs font-mono').style(
                'color:#8c959f;max-width:340px;word-break:break-all')
            with ui.row().classes('w-full justify-end gap-2 mt-2'):
                ui.button('Cancel', on_click=lambda: d.submit('cancel')).props('flat no-caps')
                ui.button('Delete', color='negative',
                          on_click=lambda: d.submit('ok')).props('no-caps')
        if await d == 'ok':
            self.delete_row(row_id)

    # ── existing helpers below ───────────────────────────────────────────────

    def _patch_node_role(self, node_name: str, role: str) -> None:
        """Update the saved node role in a worker thread without blocking the caller."""
        def _write():
            """Persist the role through the topology service and log any failure."""
            try:
                self._topo_app_service.patch_node_role(node_name, role)
            except Exception as e:
                self.get_logger().error(f'_patch_node_role failed: {e}')
        threading.Thread(target=_write, daemon=True).start()

    # ── UI shell ──────────────────────────────────────────────────────────────

    def content(self) -> None:
        """Build the Run, Plan, Sim, and System tabs, initially selecting Run."""
        if _APP_CSS:
            ui.add_head_html(f'<style>{_APP_CSS}</style>')
        with ui.tabs().classes('w-full') as tabs:
            tab_run     = ui.tab('Run',     icon='route')
            tab_plan    = ui.tab('Plan',    icon='checklist')
            tab_sim     = ui.tab('Sim',     icon='view_in_ar')
            tab_system  = ui.tab('System',  icon='settings')
        with ui.tab_panels(tabs, value=tab_run).classes('w-full'):
            with ui.tab_panel(tab_run):
                self._run_content()
            with ui.tab_panel(tab_plan):
                self._plan_content()
            with ui.tab_panel(tab_sim):
                SimPage(self._sim_vm, self._topo_vm)
            with ui.tab_panel(tab_system):
                SystemPage(
                    telemetry_vm=self._telemetry_vm,
                    robot_brain_vm=self._robot_brain_vm,
                )

    # ── Run tab ───────────────────────────────────────────────────────────────

    def _run_content(self) -> None:

        """Builds the navigation interface and keeps its displayed state synchronized with the robot
        and topology.
        """
        # A dropped connection never delivers the joystick's on_end, which would leave the last
        # cmd_vel in force. release_joystick only stops a joystick that still holds a command.
        ui.context.client.on_disconnect(self._run_vm.release_joystick)

        with ui.row().classes('w-full gap-3 items-stretch'):

            with ui.column().classes('flex-1 gap-3').style('min-width:0'):

                with ui.row().classes('w-full gap-3 items-stretch'):

                    JoystickControlCard(
                        global_vm=self._global_vm,
                        run_vm=self._run_vm,
                    )

                    NodeMapCard(
                        topo_vm=self._topo_vm,
                        pose_state=self._run_vm.node_map,
                    )

                with ui.row().classes('w-full gap-3 items-start'):

                    TrackCard(
                        state=self._run_vm.track,
                        on_start=self.start_track,
                        on_stop=self.stop_track
                    )

                    DropNodeCard(
                        state=self._run_vm.drop_node,
                        topo_vm=self._topo_vm,
                        on_drop=self.drop_topo_node,
                        on_row_action=self.set_row_action,
                    )

                    RowDiscoveryCard(
                        state=self._run_vm.discovery,
                        on_start=self.start_discovery,
                        on_stop=self.stop_discovery,
                    )

                    # OBSTACLE: Mark Obstacle card next to Drop Node
                    attach_nav_card(self._obstacle_vm)

            navigation_sidebar = NavigationSidebar(
                global_store=self._global_vm,
                topo_vm=self._topo_vm,
                nav_state=self._run_vm.topo,
                on_go=lambda:
                    self.send_nav_goal(self._topo_vm.selected_node)
                    if self._topo_vm.selected_node else None,
                on_cancel=self.cancel_nav_goal,
                on_delete=lambda: self.confirm_delete_node(self._topo_vm.selected_node),
                on_select=self._topo_vm.set_selected_node,
            )

        def on_node_clicked(e) -> None:
            """Selects the clicked topology node when it exists in the current map."""
            n = (e.args or {}).get('node')
            if n and self._topo_doc and self._topo_doc.has_node(n):
                self._topo_vm.set_selected_node(n)
        ui.on('topo_node_clicked', on_node_clicked)

        _prev: dict = {}

        def refresh_nav() -> None:
            """
            Refresh the navigation view with the latest robot pose, topology, and navigation state.
            """
            self._telemetry_vm.refresh()
            self._run_vm.update_pose_label(self._telemetry_vm.odom, self._telemetry_vm.gps)

            topo_doc = self._topo_doc
            if topo_doc is None:
                return

            current_node = self._topo_vm.current_node

            rp = self._telemetry_vm.robot_pose()
            rp_key = None if rp is None else (round(rp[0], 1), round(rp[1], 1),
                                            round(rp[2], 2))
            self._run_vm.node_map.robot_pose = rp

            snap = {
                'sel': self._topo_vm.selected_node,
                'cur': current_node,
                'stat': self._run_vm.topo.nav_status,
                'nav': self._run_vm.topo.navigating,
                'nodes': set(topo_doc.nodes),
                'robot': rp_key
            }
            nonlocal _prev
            changed = {k for k, v in snap.items() if _prev.get(k) != v}
            if not changed:
                return
            _prev.update(snap)

            if changed & {'sel', 'nodes'}:
                navigation_sidebar.render_nodes(
                    topo_doc.nodes,
                    self._topo_vm.selected_node,
                )

        ui.timer(0.2, refresh_nav)

    # ── Plan tab ──────────────────────────────────────────────────────────────

    def _plan_content(self) -> None:
        """Build the Plan tab UI: field boundary drawing, F2C planning, and row saving."""
        state = self._plan_vm.open_page()
        with ui.row().classes('w-full gap-3 items-start mb-3'):
            gps = ((self.latest_gps.latitude, self.latest_gps.longitude)
                   if self.latest_gps else None)
            boundary = BoundaryMapCard(state, gps)
            controls = PlanControlsCard(state, self._plan_vm, self._obstacle_vm, boundary.map)
        draw_handle = controls.draw_handle

        # ─────────────────────────────────────────────────────────────────────
        # MISSION QUEUE CARD
        # ─────────────────────────────────────────────────────────────────────

        with ui.card().classes('w-full'):
            with ui.row().classes('items-baseline gap-2 mb-3'):
                ui.label('Mission Queue').classes('font-semibold')
                ui.label('select rows · pick action · run').classes(
                    'text-xs').style('color:#8c959f')

            # ── action selector + param editor ────────────────────────────────
            with ui.row().classes('items-center gap-3 w-full mb-2 flex-wrap'):
                ui.html('<div class="sec-label" style="white-space:nowrap">Implement</div>')
                action_select = ui.select(
                    options={k: f'{a.icon} {a.label}' for k, a in ACTIONS.items()},
                    value='drive',
                ).classes('flex-1').props('dense outlined')

            # Param inputs rendered dynamically when action changes.
            param_row = ui.row().classes('items-end gap-3 w-full flex-wrap mb-2')
            param_inputs: dict = {}   # key → ui.number widget

            def _rebuild_params():
                param_row.clear()
                param_inputs.clear()
                action = ACTIONS.get(action_select.value)
                if action is None or not action.param_schema:
                    return
                with param_row:
                    for p in action.param_schema:
                        inp = ui.number(
                            label=f'{p.label} ({p.unit})',
                            value=p.default,
                            min=p.min, max=p.max, step=p.step,
                            precision=p.precision,
                        ).classes('w-32').props('dense outlined')
                        param_inputs[p.key] = inp

            action_select.on_value_change(lambda _: _rebuild_params())
            _rebuild_params()

            # ── available / queue panels ──────────────────────────────────────
            with ui.row().classes('w-full gap-4 items-start'):
                with ui.card().classes('flex-1').style('background:#f6f8fa;padding:10px'):
                    ui.html('<div class="sec-label mb-2">Available rows</div>')
                    available_col = ui.column().style('gap:2px;width:100%')
                with ui.card().classes('flex-1').style('background:#f6f8fa;padding:10px'):
                    ui.html('<div class="sec-label mb-2">Today\'s queue</div>')
                    queue_col = ui.column().style('gap:2px;width:100%')
                    queue_lbl = ui.label('Empty — add rows from the left').classes(
                        'text-xs').style('color:#8c959f')

            # mission_queue: list of (row_id, action_key, action_params) triples
            mission_queue: list = []

            def _render_queue():
                queue_col.clear()
                queue_lbl.set_visibility(not mission_queue)
                if not mission_queue:
                    return
                with queue_col:
                    for i, (rid, act, params) in enumerate(mission_queue):
                        idx = i
                        adef = ACTIONS.get(act)
                        param_str = ' · '.join(
                            f'{p.label}: {params.get(p.key, p.default):.{p.precision}f}{p.unit}'
                            for p in (adef.param_schema if adef else [])
                        )
                        with ui.row().classes('items-center gap-1 w-full'):
                            with ui.column().classes('flex-1 gap-0'):
                                ui.label(
                                    f'Row {rid}  {adef.icon if adef else "?"} '
                                    f'{adef.label if adef else act}'
                                ).classes('text-sm font-mono')
                                if param_str:
                                    ui.label(param_str).classes('text-xs font-mono').style(
                                        'color:#8c959f')
                            ui.button('↑',
                                on_click=lambda _, i=idx: _move(i, -1)).props(
                                'flat dense').classes('text-xs').style(
                                'color:#57606a').set_enabled(i > 0)
                            ui.button('↓',
                                on_click=lambda _, i=idx: _move(i, 1)).props(
                                'flat dense').classes('text-xs').style(
                                'color:#57606a').set_enabled(i < len(mission_queue) - 1)
                            ui.button('✕',
                                on_click=lambda _, i=idx: _remove(i)).props(
                                'flat dense').classes('text-xs').style('color:#cf222e')

            def _move(idx, d):
                ni = idx + d
                if 0 <= ni < len(mission_queue):
                    mission_queue[idx], mission_queue[ni] = mission_queue[ni], mission_queue[idx]
                _render_queue()

            def _remove(idx):
                mission_queue.pop(idx)
                _render_queue()

            def _add_row(row_id):
                act = action_select.value or 'drive'
                adef = ACTIONS.get(act)
                params = {p.key: float(param_inputs[p.key].value)
                          for p in (adef.param_schema if adef else [])
                          if p.key in param_inputs}
                # allow duplicates — operator may want to run the same row
                # twice with different implements (e.g. spray then harvest)
                mission_queue.append((row_id, act, params))
                _render_queue()

            mission_status = ui.label('').classes('text-xs font-mono mt-3').style(
                'color:#57606a')

            with ui.row().classes('gap-2 mt-3'):
                run_btn = ui.button(
                    'Run Mission',
                    on_click=lambda: self._run_mission(mission_queue, mission_status),
                ).props('color=positive no-caps')
                ui.button(
                    'Cancel',
                    on_click=self.cancel_mission,
                ).props('color=negative no-caps flat')

            # ── available rows refresh ────────────────────────────────────────
            _avail_prev: list[set[TopoNode]] = [set()]
            def _refresh_available():
                """Refresh available rows after the topology snapshot stabilizes."""
                nonlocal _avail_prev
                topo_doc = self._topo_doc
                if topo_doc is None:
                    return
                snap = set(topo_doc.nodes)
                prev, _avail_prev[0] = _avail_prev[0], snap
                if snap != prev:
                    return
                rows: dict[int, str] = {}
                for nd in topo_doc.nodes:
                    meta = nd.meta
                    rid  = meta.get('row_id')
                    if rid is not None and meta.get('row_role', '') == 'entry':
                        try:
                            rows[int(rid)] = nd.name
                        except (TypeError, ValueError):
                            pass
                available_col.clear()
                if not rows:
                    with available_col:
                        ui.label('No rows in map yet').classes('text-xs').style(
                            'color:#8c959f')
                    return
                with available_col:
                    for rid in sorted(rows):
                        r = rid
                        with ui.row().classes('items-center gap-2 w-full'):
                            ui.label(f'Row {rid}').classes('text-sm font-mono flex-1')
                            ui.label(rows[rid]).classes('text-xs font-mono').style(
                                'color:#8c959f')
                            ui.button(
                                'Add →',
                                on_click=lambda _, r=r: _add_row(r),
                            ).props('color=primary outline no-caps dense')
                            ui.button(
                                '✕',
                                on_click=lambda _, r=r: self.confirm_delete_row(r),
                            ).props('flat dense').classes('text-xs').style('color:#cf222e')

            ui.timer(1.0, _refresh_available)

            # ── mission store panel ───────────────────────────────────────────
            ui.separator().classes('my-3')
            with ui.row().classes('items-baseline gap-2 mb-2'):
                ui.label('Mission Store').classes('font-semibold')
                ui.label('saved missions with repeat schedules').classes(
                    'text-xs').style('color:#8c959f')

            with ui.card().classes('w-full').style('background:#f6f8fa;padding:10px'):
                missions_col = ui.column().style('gap:2px;width:100%')
                missions_empty_lbl = ui.label(
                    'No saved missions'
                ).classes('text-xs').style('color:#8c959f')

            # Save current queue as a named recurring mission
            with ui.row().classes('items-center gap-2 mt-2 flex-wrap'):
                save_name_input = ui.input(
                    placeholder='Mission name', label='Save queue as…',
                ).classes('flex-1').props('dense outlined')
                save_repeat = ui.number(
                label='Repeat (h)', value=None, min=1, step=1, precision=0,
                ).classes('w-24').props('dense outlined clearable').tooltip(
                    'Leave blank for one-shot')

                def _save_mission():
                    """Save the queue using the entered name and optional repeat interval in hours."""
                    rpt = int(save_repeat.value) if save_repeat.value else None
                    self._mission_vm.save_queue(
                        mission_queue, save_name_input.value or '', rpt)

                ui.button('Save', on_click=_save_mission).props(
                    'color=primary no-caps dense')

            mission_store_status = ui.label('').classes('text-xs font-mono mt-1').style(
                'color:#57606a')

            _mstore_prev = [-1]
            def _refresh_missions():
                """Refresh status; rebuild the saved list and run button when the version changes."""
                # Store status line
                cur_status = self._mission_vm.status
                mission_store_status.set_text(cur_status)
                mission_store_status.style(
                    'color:#cf222e' if cur_status.startswith('ERROR') else
                    'color:#1a7f37' if cur_status else 'color:#57606a')

                v = self._mission_vm.version
                if v == _mstore_prev[0]:
                    return
                _mstore_prev[0] = v

                snap = self._mission_vm.missions
                missions_col.clear()
                missions_empty_lbl.set_visibility(not snap)
                run_btn.set_enabled(not self._mission_vm.running)

                if not snap:
                    return
                with missions_col:
                    for m in snap:
                        mid   = m.get('id', '?')
                        name  = m.get('name', mid)
                        act   = m.get('action', '—')
                        adef  = ACTIONS.get(act)
                        icon  = adef.icon if adef else '?'
                        rows  = m.get('rows', [])
                        active = m.get('active', False)
                        due_h = self._mission_vm.next_due_in_hours(mid)
                        if due_h is None:
                            due_str, due_col = 'done', 'color:#8c959f'
                        elif due_h == 0.0:
                            due_str, due_col = 'due now', 'color:#1a7f37'
                        else:
                            due_str, due_col = f'in {due_h:.1f}h', 'color:#9a6700'
                        last_ok = m.get('last_run_success')
                        last_str = '✓' if last_ok is True else '✗' if last_ok is False else '—'

                        with ui.row().classes('items-center gap-2 w-full'):
                            ui.label(f'{icon} {name}').classes(
                                'text-sm font-mono').style('min-width:100px')
                            ui.label(f'{len(rows)} rows').classes(
                                'text-xs font-mono flex-1').style('color:#8c959f')
                            ui.label(due_str).classes('text-xs font-mono').style(due_col)
                            ui.label(last_str).classes('text-xs font-mono').style(
                                'color:#1a7f37' if last_ok is True else
                                'color:#cf222e' if last_ok is False else 'color:#8c959f')
                            act_toggle = ui.checkbox('', value=active).props('dense')
                            act_toggle.tooltip('Active — included in today_queue()')
                            act_toggle.on_value_change(
                                lambda e, m=mid: self._mission_vm.set_active(m, e.value))
                            ui.button(
                                '✕',
                                on_click=lambda _, m=mid: self._mission_vm.delete(m),
                            ).props('flat dense').classes('text-xs').style('color:#cf222e')

            ui.timer(0.5, _refresh_missions)

        # OBSTACLE: obstacle list + map rendering, full width below the queue.
        attach_mission_obstacle_panel(draw_handle)

# ─────────────────────────────────────────────────────────────────────────────
# NiceGuiNode mission executor methods
# ─────────────────────────────────────────────────────────────────────────────

    def _run_mission(self, queue: list, status_lbl) -> None:
        """Start executing the queued row mission.

        Parameters:
            queue (list): Tuples containing a row ID, action key, and action parameters.
            status_lbl: UI status label updated with mission progress and outcome.

        Report an empty queue, an active mission, unavailable navigation, or MissionError
        in the label and return; other startup errors propagate.
        Valid missions run on a worker thread.
        """
        def show(level: str, text: str) -> None:
            """Set the status label's text and color for a mission status level."""
            status_lbl.set_text(text)
            status_lbl.style(f'color:{MISSION_STATUS_COLOURS[level]}')

        if not queue:
            show(ERROR, 'ERROR: queue is empty')
            return
        if self._mission_service.running:
            show(ERROR, 'ERROR: mission already running')
            return
        if not self._run_vm.navigation_available:
            show(ERROR, 'ERROR: action client unavailable')
            return
        try:
            steps = resolve_steps(self._topo_doc, queue)
            self._mission_service.start(
                steps, self._send_goal_sync, show,
                lambda: self._global_vm.soft_estop_active)
        except MissionError as exc:
            show(ERROR, f'ERROR: {exc}')

    def _send_goal_sync(self, target: str) -> bool:
        """Navigate to a topology node and block until it ends.

        Call from a worker thread.
        Use a 300-second wait budget after sending the goal, excluding the server wait.
        Mission cancellation or soft e-stop requests cancellation while waiting.
        See NavigationApplicationService.navigate_and_wait for polling and cancellation timing.
        Synchronous navigation errors propagate.

        Returns:
            bool: True if the robot arrived, False if navigation failed, was cancelled, timed
            out, or the action server was unavailable.
        """
        return self._run_vm.navigate_and_wait(
            target, NAV_TIMEOUT_SEC,
            lambda: self._mission_service.cancel_requested or self._global_vm.soft_estop_active)

    def cancel_mission(self) -> None:
        """Signal the executor to stop before its next goal and cancel the active goal."""
        self._mission_service.cancel()
        self.cancel_nav_goal()

    # ── Map archive ───────────────────────────────────────────────────────────

    def set_row_action(self, action: str) -> None:
        """Switch row driving between geometry-only and vision following.

        Applies to rows added from now on and rewrites the row edges already in
        the map, so the same map can be seeded with row_traversal and then run
        with limbic_row_follow once a crop exists.

        Ignore unsupported or unchanged actions. Update the session selection immediately;
        if the loaded map has matching edges to change, persist and reload in the background.
        Worker errors are reported in drop_node.status without reverting the selection.
        """
        if action not in (ROW_ACTION, VISION_ROW_ACTION) or action == self._row_action:
            return
        self._row_action = action
        self._run_vm.drop_node.row_action = action
        if not self._topo_doc:
            return
        n_edges = sum(1 for n in self._topo_doc.nodes for e in n.edges
                      if e.action in (ROW_ACTION, VISION_ROW_ACTION) and e.action != action)
        if n_edges == 0:
            self._run_vm.drop_node.status = f'row action: {action} (no edges to change)'
            return

        def _modify(doc):
            """Apply the selected driving action to the document's row edges."""
            doc.set_row_action({ROW_ACTION, VISION_ROW_ACTION}, action)

        self._persist_and_reload(
            _modify, self._run_vm.drop_node, 'status',
            f'row action: {action}, {n_edges} row edges updated')

    def _on_map_archived(self) -> None:
        """Return row driving to geometry mode after the map was archived and cleared."""
        self._row_action = ROW_ACTION
        self._run_vm.drop_node.row_action = ROW_ACTION

    def toggle_estop(self) -> None:
        """
        Toggle the soft emergency-stop state and publish the updated value.
        """
        self._global_vm.toggle_estop()

    def send_speed(self, x: float, y: float) -> None:
        """Publish a velocity command, propagating publisher errors.

        Parameters:
            x (float): Forward linear velocity in meters per second.
            y (float): Turn command in radians per second; published as angular.z = -y.
        """
        self._run_vm.move_joystick(x, y)


# ── entrypoints ───────────────────────────────────────────────────────────────

def main() -> None:
    pass


def ros_main() -> None:
    rclpy.init()
    node = NiceGuiNode()
    try:
        rclpy.spin(node)
    except ExternalShutdownException:
        pass


app.on_startup(lambda: threading.Thread(target=ros_main).start())
ui_run.APP_IMPORT_STRING = f'{__name__}:app'
# reload=False is mandatory here. This module is imported (never run as
# __main__) by the `ui_node` console-script entry point, so there's no
# __name__ guard around this call -- every import executes it. NiceGUI's
# default reload=True spins up uvicorn's reload supervisor, which re-imports
# this module in a worker process to load `app`; that re-import re-runs this
# exact line a second time and tries to bind port 80 again while the
# supervisor still holds it -> EADDRINUSE. Hot-reload also has no use case
# in a container that gets rebuilt/restarted on code changes anyway.
ui.run(favicon='🤖', port=80, reload=False)
