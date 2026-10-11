# pylint: disable=duplicate-code,too-many-lines,consider-using-with
"""
ui_node.py — Sowbot web cockpit on :80
"""

import io
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import traceback
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape
from importlib import resources
from pathlib import Path

# The following imports get generated in the Dockerfile, they aren't available to pylint
# pylint: disable=import-error
import rclpy
from ament_index_python.packages import (
    PackageNotFoundError,
    get_package_share_directory,
)
from nav_msgs.msg import Odometry
from nicegui import app, ui, ui_run
from nicegui import run as ng_run
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import NavSatFix
from std_msgs.msg import Bool, Float64, String

# F2C: lat/lon<->XY projection + swath generator, now a standalone package
# (devkit_f2c_planner) — see its f2c_planner.py docstring for why.
from devkit_f2c_planner.f2c_planner import _run_f2c
from devkit_ui import plan_import

# MISSION: store owns missions.yaml, scheduling, and run recording.
from devkit_ui.actions import ACTIONS, action_ros_msgs
from devkit_ui.application_services.drive_application_service import DriveApplicationService
from devkit_ui.application_services.navigation_application_service import (
    NavigationApplicationService,
)
from devkit_ui.application_services.robot_brain_application_service import RobotBrainApplicationService
from devkit_ui.application_services.row_discovery_application_service import (
    RowDiscoveryApplicationService,
)
from devkit_ui.application_services.telemetry_application_service import (
    TelemetryApplicationService,
)
from devkit_ui.application_services.topology_application_service import (
    TopologyApplicationService,
)
from devkit_ui.constants import NAV_ACTION, NODE_NAME, ROW_ACTION, VISION_ROW_ACTION
from devkit_ui.domain_services.drive_domain_service import DriveDomainService
from devkit_ui.domain_services.navigation_domain_service import NavigationDomainService
from devkit_ui.domain_services.robot_brain_domain_service import RobotBrainDomainService
from devkit_ui.domain_services.row_discovery_domain_service import RowDiscoveryDomainService
from devkit_ui.domain_services.telemetry_domain_service import TelemetryDomainService
from devkit_ui.domain_services.topology_domain_service import TopologyDomainService
from devkit_ui.missions import MissionStore
from devkit_ui.models import (
    TopoDoc,
    TopoEdge,
    TopoNode,
    TopoProperties,
    Vector2,
)

# pylint: enable=import-error
# OBSTACLE: obstacle manager + UI attachment helpers
from devkit_ui.obstacles import (
    ObstacleManager,
    attach_mission_obstacle_panel,
    attach_mission_sidebar_controls,
    attach_nav_card,
)
from devkit_ui.pages.run.drop_node_card import DropNodeCard
from devkit_ui.pages.run.joystick_control_card import JoystickControlCard
from devkit_ui.pages.run.navigation_sidebar import NavigationSidebar
from devkit_ui.pages.run.node_map_card import NodeMapCard
from devkit_ui.pages.run.row_discovery_card import RowDiscoveryCard
from devkit_ui.pages.run.track_card import TrackCard
from devkit_ui.ros_gateway import RosGateway
from devkit_ui.row_planning import plan_contour_rows
from devkit_ui.topo_row_builder import (
    RowAnchor,
    RowBuildSettings,
    add_missing_edges,
    apply_row_nodes,
    build_row_nodes,
    plan_row_repair,
)
from devkit_ui.view_models.global_view_model import GlobalViewModel
from devkit_ui.view_models.run_view_model import RunViewModel
from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel
from devkit_ui.view_models.topology_view_model import TopologyViewModel

# Field 27's actual GPS extent, derived from maps/recon_logs/recon.csv (the
# real Agri-Field-Dataset field-27 mesh, Zenodo 7805321 — France, ~372m x
# 252m footprint, downsampled to a ~6.5m grid — replacing an earlier
# placeholder India location that was in this file before). FIELD27_CENTER
# is the anchor the mesh was georeferenced against (the field's actual
# centroid). FIELD27_BOUNDS pads that extent by 15% on each side so
# leaflet's fitBounds() shows the whole field with a small margin, rather
# than butting the boundary against the map edge. This value MUST stay in
# lockstep with DEFAULT_FIELD_LAT/LON in topo_to_forest3d.py,
# FIELD_DATUM_LAT/LON's default in manage.py, and --anchor-lat/lon's
# default in maps/recon_logs/test_contour_planning.py — see
# _FAKE_GPS_LAT/LON below for why a mismatch there is dangerous, not just
# cosmetic.
FIELD27_CENTER = (48.0046000, 3.6644000)
FIELD27_BOUNDS = ((48.0031957, 3.6612233), (48.0060043, 3.6675767))

_NAME_RE = re.compile(r'^[A-Z0-9_]+$')


# ── Import CSS ────────────────────────────────────────────────────────────────

def load_css() -> str:
    """Return the bundled app stylesheet from the package resources."""
    try:
        return resources.files('devkit_ui').joinpath('css/app.css').read_text(encoding='utf-8')
    except FileNotFoundError:
        return ''


_APP_CSS = load_css()

# ── Tools card: process registry shared by every browser session ────────
# Handles live here, not in the page closure, so reloading or opening a
# second tab cannot orphan a running tool or spawn a duplicate.
_TOOLS: dict = {}
_GRAPHER_DIR = '/tmp/ros2grapher'


def _shared(name: str, factory: Callable):
    """Return the shared tool entry, storing the factory result if absent."""
    return _TOOLS.setdefault(name, factory())


def _alive(proc) -> bool:
    """Return whether a process handle exists and is still running."""
    return proc is not None and proc.poll() is None


def _spawn_logged(cmd: list, log: str, **kwargs):
    """Start cmd in its own session with output going to a log file."""
    with open(log, 'w', encoding='utf-8') as fh:
        return subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True, **kwargs)


def _kill_group(proc, grace: float = 5.0) -> None:
    """SIGTERM then SIGKILL a process group. Blocking: call off the UI loop."""
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _stop_all(procs: list) -> None:
    """Stop the listed process groups and clear the list; blocks while waiting."""
    for proc in procs:
        _kill_group(proc, grace=3.0)
    procs.clear()


def _ensure_xvfb(display: str, daemons: list) -> None:
    """Start Xvfb unless a live one owns the display; clear stale locks."""
    num = display.lstrip(':')
    lock = f'/tmp/.X{num}-lock'
    if os.path.exists(lock):
        if subprocess.run(['pgrep', '-f', f'[X]vfb {display}'],
                          capture_output=True, check=False).returncode == 0:
            return
        for path in (lock, f'/tmp/.X11-unix/X{num}'):
            try:
                os.remove(path)
            except OSError:
                pass
    daemons.append(_spawn_logged(
        ['Xvfb', display, '-screen', '0', '1920x1080x24', '-nolisten', 'tcp'],
        f'/tmp/xvfb{num}.log'))
    time.sleep(0.5)


def _start_vnc_stack(display: str, vnc_port: int, web_port: int, daemons: list) -> None:
    """Xvfb + x11vnc (loopback only) + noVNC websockify. Blocking."""
    _stop_all(daemons)
    _ensure_xvfb(display, daemons)
    daemons.append(_spawn_logged(
        ['x11vnc', '-display', display, '-nopw', '-forever', '-shared', '-quiet',
         '-localhost', '-rfbport', str(vnc_port)], f'/tmp/x11vnc-{vnc_port}.log'))
    daemons.append(_spawn_logged(
        ['websockify', '--web', '/usr/share/novnc', str(web_port), f'localhost:{vnc_port}'],
        f'/tmp/websockify-{web_port}.log'))
    time.sleep(0.5)


def _restore_label(lbl, proc) -> None:
    if _alive(proc):
        lbl.set_text(f'running (pid {proc.pid})')
        lbl.style('color:#1a7f37')


def _report_if_exited(proc, lbl, log: str) -> None:
    """Two seconds after start, say so if the process already died."""
    def check() -> None:
        if proc.poll() is not None:
            lbl.set_text(f'exited ({proc.returncode}) - see {log}')
            lbl.style('color:#cf222e')
    ui.timer(2.0, check, once=True)


def _shutdown_tools() -> None:
    """Stop registered tool process groups during application shutdown."""
    for entry in _TOOLS.values():
        for proc in (entry if isinstance(entry, list) else [entry]):
            if isinstance(proc, subprocess.Popen):
                _kill_group(proc, grace=3.0)


app.on_shutdown(_shutdown_tools)

# ── ROS node ──────────────────────────────────────────────────────────────────

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
        self._robot_brain_app_service = RobotBrainApplicationService(self._robot_brain_domain_service)
        self._row_discovery_app_service = RowDiscoveryApplicationService(
            self._row_discovery_domain_service)
        self._nav_app_service = NavigationApplicationService(self._nav_domain_service)
        self._telemetry_app_service = TelemetryApplicationService(
            self._telemetry_domain_service)

        # Initialize view models
        self._global_vm = GlobalViewModel(self._drive_app_service)
        self._run_vm = RunViewModel(
            self._drive_app_service, self._row_discovery_app_service, self._nav_app_service)
        self._topo_vm = TopologyViewModel(self._topo_app_service)
        self._telemetry_vm = TelemetryViewModel(self._telemetry_app_service)

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

        self._f2c_swaths:     list  = []
        self._f2c_row_start:  int   = 1
        self._f2c_tool_width: float = 1.2
        self._f2c_angle_deg:  float = 0.0
        self._f2c_contour_used: bool = False
        self._f2c_origin_ll = None
        self._f2c_break_after: set = set()
        self.f2c_save_status: str   = ''

        # OBSTACLE: manager owns obstacles.yaml + /obstacles publisher.
        # Attach after latest_odom / latest_gps fields exist so the
        # manager can read them when projecting to the map frame.
        self._obstacle_mgr = ObstacleManager()
        self._obstacle_mgr.attach(self)

        # MISSION: store owns missions.yaml, scheduling, and run recording.
        # Attach after obstacle manager so node attributes are all present.
        self._mission_store = MissionStore()
        self._mission_store.attach(self)

        # Lazy cache of std_msgs/Bool publishers for tool topics, keyed by
        # topic name.  Created on first use by _get_tool_publisher().
        self._tool_publishers: dict = {}

        # Mission executor state.  A running mission sets _mission_running
        # True; the executor thread clears it when done (or cancelled).
        self._mission_running:   bool          = False
        self._mission_cancel:    bool          = False
        self._mission_run_id:    str | None = None   # active MissionStore id

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
        """
                       Add a topology node at the current robot position and persist it to the
                       active map.

                       Add the node in memory immediately, then save and reload in a background
                       thread. Use (0, 0) in simulation if odometry is unavailable. Connect to the
                       current node, or the selected node as a fallback; save the outgoing edge
                       only if its target is already on disk. Row nodes use the selected row action.
                       Validation failures and worker exceptions set drop_node.status; a failed
                       save does not roll back the initial in-memory addition.

                       Parameters:
                        name (str): Name, stripped and uppercased, with spaces replaced by
                        underscores and characters outside A-Z, 0-9, and underscore removed.
                        row_id (int | None): Row identifier to associate with the node, or None for
                        a navigation node.
                        row_role (str): Role of the node within its row, such as "entry" or "exit".
                       """
        name = re.sub(r'[^A-Z0-9_]', '', name.strip().upper().replace(' ', '_'))
        if not name:
            self._run_vm.drop_node.status = 'ERROR: node name required'
            return
        if not _NAME_RE.match(name):
            self._run_vm.drop_node.status = f'ERROR: invalid name "{name}"'
            return
        if not self._topo_doc:
            self._run_vm.drop_node.status = 'ERROR: map not loaded'
            return
        if self._topo_doc.has_node(name):
            self._run_vm.drop_node.status = f'ERROR: {name} already exists'
            return
        if self.latest_odom is None and not self._is_sim:
            self._run_vm.drop_node.status = 'ERROR: no odometry'
            return

        x = round(self.latest_odom.pose.pose.position.x, 3) if self.latest_odom else 0.0
        y = round(self.latest_odom.pose.pose.position.y, 3) if self.latest_odom else 0.0
        current_node = self._topo_vm.current_node
        connect_to = (current_node
                      if current_node not in ('—', 'none', 'None', '', None) else None)
        if connect_to and not self._topo_doc.has_node(connect_to):
            connect_to = None
        selected_node = self._topo_vm.selected_node
        if not connect_to and selected_node and self._topo_doc.has_node(selected_node):
            connect_to = selected_node
        map_name  = self._topo_doc.name
        nav_frame = self._topo_doc.transformation.get('topo_frame_id') or 'map'
        is_row    = row_id is not None

        if is_row:
            edge_action, xy_tol, yaw_tol, vert_r = self._row_action, 0.1, 0.05, 0.5
        else:
            edge_action, xy_tol, yaw_tol, vert_r = NAV_ACTION, 0.3, 0.1,  1.0

        gps = self.latest_gps
        gps_meta: dict = {}
        if gps is not None and gps.status.status >= 0:
            gps_meta = {
                'gps_lat': round(gps.latitude, 7),
                'gps_lon': round(gps.longitude, 7),
                'gps_fix_type': int(gps.status.status),
                'gps_hdop': None,
            }

        row_meta: dict = {}
        if is_row:
            row_meta = {
                'row_id': row_id,
                'row_role': row_role,
            }

        new_node = TopoNode(
            name=name,
            nav_frame=nav_frame,
            x=x,
            y=y,
            meta={
                'map': map_name,
                'node': name,
                'pointset': map_name,
                'dropped_by': 'webui',
                'timestamp': datetime.now(UTC).strftime('%d-%m-%Y_%H-%M-%S'),
                **gps_meta,
                **row_meta
            },
            properties=TopoProperties(xy_goal_tolerance=xy_tol, yaw_goal_tolerance=yaw_tol),
            verts=[
                Vector2(x=-vert_r, y=-vert_r),
                Vector2(x=vert_r, y=-vert_r),
                Vector2(x=vert_r, y=vert_r),
                Vector2(x=-vert_r, y=vert_r),
            ],
            edges=[
                TopoEdge(action=edge_action, edge_id=f'{name}_{connect_to}', node=connect_to),
            ] if connect_to else [],
        )
        self._topo_doc.add_node(new_node)

        conn_str = f' → {connect_to}' if connect_to else ''
        gps_str  = (f' [{gps_meta["gps_lat"]:.5f},{gps_meta["gps_lon"]:.5f}]'
                    if gps_meta else '')
        row_str  = f' row={row_id}/{row_role}' if is_row else ''
        self._run_vm.drop_node.status = (
            f'{name}{conn_str} at ({x}, {y}){row_str}{gps_str} — writing…')

        def _publish_and_persist():
            """Save the dropped node and make the updated map available to navigation.

            The service creates a missing map from defaults, saves only edges to nodes
            already on disk and adds no reverse edges. A duplicate name on disk skips
            writing. Worker exceptions become drop_node.status errors.
            """
            try:
                base = f'{name}{conn_str} at ({x},{y}){row_str}{gps_str}'

                def _saved() -> None:
                    """Show reloading status after saving the node and before switching maps."""
                    self._run_vm.drop_node.status = f'{base} — reloading…'

                result = self._topo_app_service.drop_node(new_node, on_saved=_saved)
                if result.kind == 'skipped':
                    self.get_logger().warn(f'Node {name} already in file — skipping write')
                    return
                if result.kind == 'switch_failed':
                    self._run_vm.drop_node.status = (
                        f'{name}{conn_str} saved (switch failed: {result.detail})')
                    self.get_logger().warn(
                        f'switch_topological_map failed ({result.detail})')
                else:
                    self._run_vm.drop_node.status = result.describe(base)
                self.get_logger().info(
                    f'Node dropped: {name} at ({x:.3f},{y:.3f}){conn_str}{row_str}{gps_str}')
            except Exception as e:
                self._run_vm.drop_node.status = f'ERROR: {e}'
                self.get_logger().error(
                    f'drop_topo_node failed: {e} ({type(e)}\n{traceback.format_exc()})')

        threading.Thread(target=_publish_and_persist, daemon=True).start()
        return

    # ── track mode ────────────────────────────────────────────────────────────

    def start_track(self, prefix: str, interval: float,
                    row_id: int | None, row_role: str | None) -> None:
        """
                    Start periodic recording of topology nodes using the specified naming prefix.

                    Parameters:
                        prefix (str): Prefix used for numbered node names after normalization.
                        interval (float): Time in seconds between recorded nodes.
                        row_id (int | None): Optional row identifier associated with each node.
                        row_role (str | None): Role assigned to recorded nodes when no row
                        identifier is provided.
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

    def import_plan_geojson(self, text: str) -> str:
        """Load a web-planner plan into the F2C state so 'Save as Topo Rows' can build nodes.

        Returns a status string; does not touch the topo map itself.
        """
        robot_ll = None
        if self.latest_gps is not None:
            lat, lon = self.latest_gps.latitude, self.latest_gps.longitude
            if math.isfinite(lat) and math.isfinite(lon) and (abs(lat) > 1e-9 or abs(lon) > 1e-9):
                robot_ll = (lat, lon)
        if robot_ll is None and not self._is_sim:
            return 'ERROR: no GPS fix; cannot check the plan is near the robot'
        try:
            plan = plan_import.parse_plan(text, robot_ll=None if self._is_sim else robot_ll)
        except plan_import.PlanError as e:
            return f'ERROR: {e}'
        self._f2c_swaths = plan.swaths
        self._f2c_origin_ll = plan.origin_ll
        self._f2c_contour_used = bool(plan.params.get('contour'))
        self._f2c_break_after = plan.break_after
        if plan.params.get('tool_width'):
            self._f2c_tool_width = float(plan.params['tool_width'])
        split = (
            f' · {len(plan.break_after)} obstacle splits (not linked)' if plan.break_after else '')
        return f'imported {len(plan.swaths)} rows{split}; review, then Save as Topo Rows'

    def save_f2c_rows_to_topo(self, prefix: str, row_id_start: int,
                              overwrite: bool = False) -> None:
        """Save the most recently planned F2C swaths as rows in the loaded topology map.

        In-row edges use the selected row action and headland edges use point-to-point
        navigation. A missing prefix, planned swaths, GPS fix or map reports an error
        in ``f2c_save_status`` and writes nothing, as do nonfinite GPS coordinates
        or coordinates both within 1e-9 degrees of zero. Hardware requires odometry;
        simulation requires the planned field origin instead. A negative GPS fix
        status alone does not prevent saving. Persistence runs in the background,
        and worker errors are also reported in ``f2c_save_status``. Errors from
        ``build_row_nodes`` propagate synchronously before persistence is scheduled.

        Args:
            prefix: Prefix for the generated node names. Surrounding whitespace is
                stripped, letters are uppercased, spaces become underscores, and
                characters outside ``A-Z``, ``0-9`` and ``_`` are removed.
            row_id_start: Row id given to the first planned swath.
            overwrite: Replace existing ``<prefix>_R*`` nodes instead of skipping
                names that are already taken.
        """
        prefix = re.sub(r'[^A-Z0-9_]', '',
                        (prefix or '').strip().upper().replace(' ', '_'))
        if not prefix:
            self.f2c_save_status = 'ERROR: prefix required'
            return
        if not self._f2c_swaths:
            self.f2c_save_status = 'ERROR: no rows planned — click Plan Rows first'
            return
        # In sim mode anchor_x/y are always 0.0 (see below), so latest_odom is
        # not actually used — skip the guard to avoid a false "no odometry" error
        # before Gazebo is launched.
        if self.latest_odom is None and not self._is_sim:
            self.f2c_save_status = 'ERROR: no odometry'
            return
        if self.latest_gps is None:
            self.f2c_save_status = 'ERROR: no GPS fix yet (/gnss/fix or sim shim)'
            return

        _lat = self.latest_gps.latitude
        _lon = self.latest_gps.longitude
        _status = self.latest_gps.status.status
        if not (math.isfinite(_lat) and math.isfinite(_lon)):
            self.f2c_save_status = (
                f'ERROR: GPS lat/lon not finite ({_lat}, {_lon}) status={_status}')
            return
        if abs(_lat) < 1e-9 and abs(_lon) < 1e-9:
            self.f2c_save_status = (
                f'ERROR: GPS lat/lon are 0,0 — no fix yet (status={_status})')
            return
        if _status < 0:
            self.get_logger().warn(
                f'save_f2c_rows: proceeding with status={_status} '
                f'(lat={_lat:.7f}, lon={_lon:.7f})')

        if not self._topo_doc:
            self.f2c_save_status = 'ERROR: map not loaded'
            return

        # Anchor for the lat/lon -> local xy conversion. On real hardware
        # latest_gps IS the survey origin and is correct. In sim, latest_gps is
        # the static datum fix, which is generally NOT where the F2C field was
        # drawn — anchoring to it offsets every row by the field-to-datum
        # distance (the "robot drove to India / off the map" bug). Anchoring to
        # the field's own reference corner instead makes the round-trip cancel,
        # so rows land at the local odom origin like get_maize_topo.py output.
        if self._is_sim and self._f2c_origin_ll is not None:
            anchor_lat, anchor_lon = self._f2c_origin_ll
        elif self._is_sim:
            self.f2c_save_status = (
                'ERROR: no F2C field origin in memory (re-run "Plan Rows" '
                'first) — saving now would anchor rows to the sim GPS '
                'datum instead of the field, offsetting every node.')
            return
        else:
            anchor_lat = self.latest_gps.latitude
            anchor_lon = self.latest_gps.longitude
        anchor = RowAnchor(
            lat=anchor_lat, lon=anchor_lon,
            x=0.0 if self._is_sim else self.latest_odom.pose.pose.position.x,
            y=0.0 if self._is_sim else self.latest_odom.pose.pose.position.y)

        current_node = self._topo_vm.current_node
        connect_to = (current_node
                      if current_node not in ('—', 'none', 'None', '', None)
                      else None)
        if connect_to and not self._topo_doc.has_node(connect_to):
            connect_to = None
        selected_node = self._topo_vm.selected_node
        if not connect_to and selected_node and self._topo_doc.has_node(selected_node):
            connect_to = selected_node

        nav_frame = self._topo_doc.transformation.get('topo_frame_id') or 'map'
        self.get_logger().info(
            f'F2C save: nav_frame={nav_frame!r}, '
            f'topo_doc type={type(self._topo_doc).__name__}')
        build = build_row_nodes(self._f2c_swaths, anchor, RowBuildSettings(
            prefix=prefix,
            row_id_start=row_id_start,
            row_action=self._row_action,
            nav_frame=nav_frame,
            map_name=self._topo_doc.name or 'mixed_test_map',
            timestamp=datetime.now(UTC).strftime('%d-%m-%Y_%H-%M-%S'),
            fix_type=int(self.latest_gps.status.status),
            overwrite=overwrite,
            contour_used=self._f2c_contour_used,
            break_after=frozenset(self._f2c_break_after),
            connect_to=connect_to,
        ))
        if not build.added:
            self.f2c_save_status = 'ERROR: nothing added (all names already taken)'
            return

        skip_str = f' (skipped {len(build.skipped)} dup ids)' if build.skipped else ''
        splice_str = f' · spliced @ {connect_to}' if connect_to else ' · standalone'
        self.f2c_save_status = (
            f'writing {len(build.added)} rows · {prefix}{splice_str}{skip_str}…')
        self._persist_and_reload(
            lambda doc: apply_row_nodes(doc, build, prefix, overwrite),
            self, 'f2c_save_status',
            f'saved {len(build.added)} rows · {prefix}{splice_str}{skip_str}',
        )

    # ── Repair row connectivity ──────────────────────────────────────────────

    def repair_row_connectivity(self, connect_to: str | None = None) -> None:
        """Rebuild missing in-row and headland edges for rows already in the loaded map.

        New in-row edges use the selected row action, and existing edge actions are
        kept. The in-memory map is updated immediately and the change is persisted in
        the background. Validation failures and worker errors are reported in
        ``f2c_save_status``. No persistence is scheduled if no edges are added.

        Parameters:
            connect_to (str | None): Optional node name used only in the success status
                text; it does not add connections. An unknown node is ignored.

        Raises:
            OverflowError: Row metadata contains an infinite row ID; propagated
                from ``plan_row_repair`` before any edges are added.
        """
        if not self._topo_doc:
            self.f2c_save_status = 'ERROR: map not loaded'
            return

        plan = plan_row_repair(self._topo_doc, self._row_action)
        if plan is None:
            self.f2c_save_status = 'ERROR: no row nodes found'
            return
        if plan.headlands_skipped:
            self.get_logger().warn(
                'repair: row nodes lack x/y coords — cannot classify headland '
                'ends; skipping headland edges (in-row edges still restored)')
        if connect_to and not self._topo_doc.has_node(connect_to):
            self.get_logger().warn(
                f'repair: connect_to={connect_to!r} not in map, ignoring')
            connect_to = None

        added_count = add_missing_edges(self._topo_doc, plan.edges)
        if added_count == 0:
            self.f2c_save_status = f'repair: already wired ({plan.row_count} rows)'
            return

        self.f2c_save_status = f'repair: adding {added_count} edges…'
        target_str = (f' @ {connect_to}' if connect_to
                      else ' — NO SPLICE, chain still isolated')
        self._persist_and_reload(
            lambda doc: add_missing_edges(doc, plan.edges),
            self, 'f2c_save_status',
            f'repair: wired {added_count} edges{target_str}',
        )

    # ── Delete topo nodes / rows ─────────────────────────────────────────────

    def delete_topo_node(self, name: str) -> None:
        """Delete a topology node and persist the updated map.

        Parameters:
            name (str): Name of the topology node to delete.
        """
        if not self._topo_doc:
            self._run_vm.topo.delete_status = 'ERROR: map not loaded'
            return
        if not name or not self._topo_doc.has_node(name):
            self._run_vm.topo.delete_status = f'ERROR: {name!r} not in map'
            return

        if self._topo_vm.selected_node == name:
            self._topo_vm.set_selected_node(None)
        self._run_vm.topo.delete_status = f'deleting {name}…'

        def _modify(file_doc):
            """
            Remove the node identified by ``name`` from the topology document.

            Parameters:
                file_doc: Topology document to modify.
            """
            file_doc.remove_node(name)

        self._persist_and_reload(
            _modify, self._run_vm.topo, 'delete_status', f'deleted {name}'
        )

    def delete_row(self, row_id: int) -> None:
        """
        Delete all topology nodes belonging to a row and persist the updated map.

        Parameters:
            row_id (int): Identifier of the row whose nodes should be deleted.
        """
        targets = {node.name for node in self._topo_doc.nodes
                   if node.meta.get('row_id') == row_id}
        if not targets:
            self._run_vm.topo.delete_status = f'ERROR: no nodes for row {row_id}'
            return
        if not self._topo_doc:
            self._run_vm.topo.delete_status = 'ERROR: map not loaded'
            return

        if self._topo_vm.selected_node in targets:
            self._topo_vm.set_selected_node(None)
        self._run_vm.topo.delete_status = f'deleting row {row_id} ({len(targets)} nodes)…'

        def _modify(file_doc):
            """
            Remove the selected nodes from a topology document.

            Parameters:
                file_doc: The topology document to modify.
            """
            file_doc.remove_nodes(targets)

        self._persist_and_reload(
            _modify,
            self._run_vm.topo,
            'delete_status',
            f'deleted row {row_id} ({len(targets)} nodes)',
        )

    # ── Confirmation dialogs ─────────────────────────────────────────────────

    async def confirm_delete_node(self, name: str | None) -> None:
        if not name or not self._topo_doc.has_node(name):
            return
        nd = self._topo_doc.get_node(name)
        rid = nd.meta.get('row_id')
        with ui.dialog() as d, ui.card():
            ui.label(f'Delete topo node "{name}"?').classes('font-semibold')
            if rid is not None:
                ui.label(
                    f'This is part of row {rid}. To delete the whole row '
                    f'(entry + exit), use the ✕ on the Mission tab instead.'
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
        if _APP_CSS:
            ui.add_head_html(f'<style>{_APP_CSS}</style>')
        with ui.tabs().classes('w-full') as tabs:
            tab_nav     = ui.tab('Nav',     icon='route')
            tab_mission = ui.tab('Mission', icon='checklist')
            tab_system  = ui.tab('System',  icon='settings')
        with ui.tab_panels(tabs, value=tab_nav).classes('w-full'):
            with ui.tab_panel(tab_nav):
                self._nav_content()
            with ui.tab_panel(tab_mission):
                self._mission_content()
            with ui.tab_panel(tab_system):
                self._system_content()

    # ── Nav tab ───────────────────────────────────────────────────────────────

    def _nav_content(self) -> None:

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
                    attach_nav_card(self, self._obstacle_mgr)

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

    # ── Mission tab ───────────────────────────────────────────────────────────

    def _mission_content(self) -> None:
        """Build the Mission tab UI: field boundary drawing, F2C planning, and row saving."""
        corners_ll: list[tuple[float, float]] = []
        swath_layers: list = []
        poly_layer:   list = [None]

        with ui.row().classes('w-full gap-3 items-start mb-3'):

            with ui.card().classes('flex-1').style('padding:10px;min-width:0'):
                ui.html('<div class="sec-label mb-2">Field boundary — click to draw</div>')
                gps_center = (
                    (self.latest_gps.latitude, self.latest_gps.longitude)
                    if self.latest_gps else FIELD27_CENTER
                )
                mission_map = ui.leaflet(center=gps_center, zoom=18).classes('w-full h-96')
                if not self.latest_gps:
                    # No live fix yet — fit the whole field extent rather
                    # than just zooming in on its centre point, so the
                    # boundary-drawing view actually shows the field.
                    mission_map.run_map_method(
                        'fitBounds', [list(FIELD27_BOUNDS[0]), list(FIELD27_BOUNDS[1])])
                mission_map.tile_layer(
                    url_template='https://server.arcgisonline.com/ArcGIS/rest/services/'
                                 'World_Imagery/MapServer/tile/{z}/{y}/{x}',
                    options={'attribution': 'Esri', 'maxZoom': 20},
                )

            with ui.card().style('width:220px;flex-shrink:0;padding:14px'):
                ui.html('<div class="sec-label">Tool width</div>')
                f2c_width = ui.number(
                    value=1.2, min=0.1, max=10.0, step=0.1, precision=2,
                    suffix='m',
                ).classes('w-full')

                ui.html('<div class="sec-label mt-3">Row angle</div>')
                f2c_angle = ui.slider(min=0, max=179, step=1, value=0).classes('w-full')
                angle_lbl = ui.label('0°').classes('text-xs font-mono').style('color:#57606a')
                f2c_angle.on('update:model-value',
                             lambda e: angle_lbl.set_text(f'{int(e.args)}°'))

                # CONTOUR: terrain-following rows instead of one fixed
                # angle — offsets from a reference elevation isoline
                # (dem.select_reference_contour_latlon()) rather than
                # F2C's SG_BruteForce. See f2c_planner._run_contour_f2c()'s
                # module comment for why this isn't a config flag on F2C.
                ui.html('<div class="sec-label mt-3">Contour rows</div>')
                f2c_contour = ui.checkbox(
                    'follow terrain (uses recon elevation log)', value=False)
                f2c_recon_path = ui.input(
                    value='/workspace/maps/recon_logs/recon.csv',
                    placeholder='/workspace/maps/recon_logs/recon.csv',
                ).classes('w-full mt-1')
                f2c_recon_path.set_visibility(False)
                dem_res_lbl = ui.html(
                    '<div class="sec-label mt-1">DEM grid resolution</div>')
                dem_res_lbl.set_visibility(False)
                f2c_dem_res = ui.number(
                    value=1.0, min=0.2, max=5.0, step=0.1, precision=2,
                    suffix='m',
                ).classes('w-full')
                f2c_dem_res.set_visibility(False)
                contour_note = ui.label(
                    'Row angle is ignored in contour mode — row direction '
                    'follows the reference elevation line instead.'
                ).classes('text-xs').style('color:#8c959f')
                contour_note.set_visibility(False)

                def _on_contour_toggle(e) -> None:
                    """Show/hide contour-only controls when the contour-mode switch flips."""
                    on = bool(e.value)
                    f2c_angle.set_enabled(not on)
                    f2c_recon_path.set_visibility(on)
                    dem_res_lbl.set_visibility(on)
                    f2c_dem_res.set_visibility(on)
                    contour_note.set_visibility(on)
                f2c_contour.on_value_change(_on_contour_toggle)

                # HEADLAND: shrink cover area by this much on all sides so
                # swaths don't start/end at the field boundary. 0 = off.
                ui.html('<div class="sec-label mt-3">Headland width</div>')
                f2c_headland = ui.number(
                    value=0.0, min=0.0, max=5.0, step=0.1, precision=2,
                    suffix='m',
                ).classes('w-full')
                ui.label('0 = no inset; ≈ tool width for one-row headland').classes(
                    'text-xs').style('color:#8c959f')

                # SNAKE: reverse every other swath so end-of-N is near
                # start-of-N+1. Default on.
                ui.html('<div class="sec-label mt-3">Snake order</div>')
                f2c_snake = ui.checkbox('reverse every other row', value=True)

                ui.html('<div class="sec-label mt-3">First row ID</div>')
                f2c_row_id_start = ui.number(
                    value=1, min=1, step=1, precision=0,
                ).classes('w-full')

                ui.html('<div class="sec-label mt-3">Row name prefix</div>')
                f2c_prefix = ui.input(
                    value='F2C', placeholder='F2C',
                ).classes('w-full')
                ui.label('→ {prefix}_R{n}_IN / _OUT').classes('text-xs font-mono').style(
                    'color:#8c959f')

                # OBSTACLE: draw-mode + shape + radius + padding + map-click
                # dispatch. Boundary-click delegates here to the local F2C
                # corner-drawing closure.
                def _boundary_click(lat: float, lon: float) -> None:
                    corners_ll.append((lat, lon))
                    mission_map.marker(latlng=(lat, lon))
                    _redraw_polygon()

                draw_handle = attach_mission_sidebar_controls(
                    self, self._obstacle_mgr, mission_map, _boundary_click)
                obstacle_pad = draw_handle.obstacle_pad

                ui.separator().classes('my-3')

                corners_lbl = ui.label('0 corners').classes('text-xs font-mono').style(
                    'color:#57606a')

                plan_btn  = ui.button('Plan Rows').props(
                    'color=positive no-caps').classes('w-full mt-2')
                save_btn  = ui.button('Save as Topo Rows').props(
                    'color=primary no-caps').classes('w-full mt-1')
                save_btn.set_enabled(False)
                async def do_import(e):
                    """Import an uploaded plan and update its status and save button."""
                    data = (await e.file.read()) if hasattr(e, 'file') else e.content.read()
                    msg = self.import_plan_geojson(data.decode('utf-8', errors='replace'))
                    f2c_status.set_text(msg)
                    f2c_status.style(
                        'color:' + ('#cf222e' if msg.startswith('ERROR') else '#1a7f37'))
                    save_btn.set_enabled(not msg.startswith('ERROR'))

                ui.upload(label='Import web plan (.geojson)', auto_upload=True,
                          on_upload=do_import).props('accept=.geojson,.json').classes('w-full mt-1')
                f2c_overwrite = ui.checkbox('Overwrite existing rows with same prefix',
                                            value=False).classes('text-xs mt-1')
                clear_btn = ui.button('Clear').props(
                    'outline no-caps').classes('w-full mt-1')

                repair_btn = ui.button('Repair Connectivity').props(
                    'outline no-caps').classes('w-full mt-1')
                repair_btn.tooltip(
                    'Wire navigate_to_pose edges between consecutive row '
                    'IN/OUT pairs. Splices into current node if localised.')

                f2c_status = ui.label('').classes('text-xs font-mono mt-2').style(
                    'color:#57606a;word-break:break-word')
                f2c_save_lbl = ui.label('').classes('text-xs font-mono mt-1').style(
                    'color:#57606a;word-break:break-word')

        # OBSTACLE: map-click is wired by attach_mission_sidebar_controls.
        # It dispatches to _boundary_click when in boundary mode, to the
        # manager otherwise. Do NOT add a second mission_map.on('map-click')
        # handler here — it would fire alongside ours and double-draw.

        def _redraw_polygon():
            if poly_layer[0] is not None:
                try:
                    poly_layer[0].run_method('remove')
                except Exception:
                    pass
                poly_layer[0] = None
            if len(corners_ll) >= 2:
                latlngs = [[lat, lon] for lat, lon in corners_ll]
                poly_layer[0] = mission_map.generic_layer(
                    name='polygon',
                    args=[latlngs,
                          {'color': '#1a7f37', 'fillOpacity': 0.15,
                           'weight': 2, 'dashArray': '6 4'}],
                )
            corners_lbl.set_text(
                f'{len(corners_ll)} corner{"s" if len(corners_ll) != 1 else ""}' +
                (' ✓' if len(corners_ll) >= 3 else ' — need 3+'))

        def do_clear():
            corners_ll.clear()
            for lyr in swath_layers:
                try:
                    lyr.run_method('remove')
                except Exception:
                    pass
            swath_layers.clear()
            if poly_layer[0] is not None:
                try:
                    poly_layer[0].run_method('remove')
                except Exception:
                    pass
                poly_layer[0] = None
            # OBSTACLE: also tear down any in-progress obstacle polygon
            draw_handle.clear_in_progress()
            corners_lbl.set_text('0 corners')
            f2c_status.set_text('')
            save_btn.set_enabled(False)
            mission_map.set_center(mission_map.center)

        clear_btn.on_click(do_clear)

        async def do_plan():
            """Run straight or contour F2C planning for the drawn boundary and render the swaths."""
            if len(corners_ll) < 3:
                f2c_status.set_text('Need at least 3 corners')
                f2c_status.style('color:#cf222e')
                return

            plan_btn.set_enabled(False)
            f2c_status.set_text('Running F2C…')
            f2c_status.style('color:#57606a')

            width      = float(f2c_width.value or 1.2)
            angle_deg  = float(f2c_angle.value or 0)
            row_start  = int(f2c_row_id_start.value or 1)
            headland_m = float(f2c_headland.value or 0.0)
            snake      = bool(f2c_snake.value)
            contour_on = bool(f2c_contour.value)

            # OBSTACLE: snapshot obstacle rings and pad
            obstacle_rings = self._obstacle_mgr.rings_ll()
            pad_m          = float(obstacle_pad.value or 0.0)

            mode_note = ''
            contour_used = False
            break_after: set[int] = set()
            try:
                if contour_on:
                    recon_path = (f2c_recon_path.value or '').strip() or \
                        '/workspace/maps/recon_logs/recon.csv'
                    dem_res = float(f2c_dem_res.value or 1.0)
                    swaths = await ng_run.io_bound(
                        plan_contour_rows, list(corners_ll), obstacle_rings,
                        width, pad_m, headland_m, snake, recon_path, dem_res,
                        break_after=break_after)
                    if swaths is None:
                        mode_note = ' · flat field, straight swaths used'
                        swaths = await ng_run.io_bound(
                            _run_f2c, list(corners_ll), obstacle_rings,
                            width, angle_deg, pad_m, headland_m, snake)
                    else:
                        mode_note = ' · contour rows'
                        contour_used = True
                else:
                    swaths = await ng_run.io_bound(
                        _run_f2c, list(corners_ll), obstacle_rings,
                        width, angle_deg, pad_m, headland_m, snake)
            except (FileNotFoundError, ValueError) as exc:
                stage = 'Contour planning' if contour_on else 'Planning'
                f2c_status.set_text(f'{stage} failed: {exc}')
                f2c_status.style('color:#cf222e')
                plan_btn.set_enabled(True)
                return
            except Exception as exc:
                self.get_logger().error(
                    f'do_plan failed: {exc} ({type(exc)}\n{traceback.format_exc()})')
                f2c_status.set_text(f'ERROR: {exc}')
                f2c_status.style('color:#cf222e')
                plan_btn.set_enabled(True)
                return

            for lyr in swath_layers:
                try:
                    lyr.run_method('remove')
                except Exception:
                    pass
            swath_layers.clear()

            for pts in swaths:
                latlngs = [[lat, lon] for lat, lon in pts]
                lyr = mission_map.generic_layer(
                    name='polyline',
                    args=[latlngs,
                          {'color': '#0969da', 'weight': 2, 'opacity': 0.85}],
                )
                swath_layers.append(lyr)

            self._f2c_swaths     = swaths
            self._f2c_row_start  = row_start
            self._f2c_tool_width = width
            self._f2c_angle_deg  = angle_deg
            self._f2c_contour_used = contour_used
            # Field reference origin = first boundary corner, the same lat0/lon0
            # _run_f2c projected from. The save path re-anchors to this so the
            # lat/lon round-trip cancels and rows land at the local odom origin
            # — instead of being offset by the distance between the field and
            # whatever latest_gps happened to read (in sim, the datum fix).
            self._f2c_origin_ll = tuple(corners_ll[0]) if corners_ll else None
            self._f2c_break_after = break_after

            hl_note  = f' · {headland_m}m headland' if headland_m > 0 else ''
            snk_note = ' · snake' if snake else ''
            obs_note = (f' · {len(obstacle_rings)} obs avoided'
                        if obstacle_rings else '')
            angle_note = '' if contour_used else f' · {angle_deg:.0f}°'
            f2c_status.set_text(
                f'{len(swaths)} rows · {width}m wide{angle_note}'
                f'{hl_note}{snk_note}{obs_note}{mode_note}')
            f2c_status.style('color:#1a7f37')
            plan_btn.set_enabled(True)
            save_btn.set_enabled(bool(swaths))

        plan_btn.on_click(do_plan)

        def do_save():
            self.save_f2c_rows_to_topo(
                f2c_prefix.value or 'F2C',
                int(f2c_row_id_start.value or 1),
                overwrite=f2c_overwrite.value,
            )
        save_btn.on_click(do_save)

        async def do_repair():
            """Open a dialog to repair row connectivity and optionally connect the repaired chain to
            a base node.
            """
            topo_doc = self._topo_doc
            if topo_doc is None:
                return
            cur = self._topo_vm.current_node
            selected = self._topo_vm.selected_node
            default_base = ''
            if cur not in ('—', 'none', 'None', '', None) and topo_doc.has_node(cur):
                default_base = cur
            elif selected and topo_doc.has_node(selected):
                default_base = selected

            row_count = sum(
                1 for nd in topo_doc.nodes
                if nd.meta.get('row_id') is not None
                and nd.meta.get('row_role') == 'entry'
            )

            with ui.dialog() as d, ui.card():
                ui.label('Repair row connectivity').classes('font-semibold')
                ui.label(
                    f'Add navigate_to_pose edges between {row_count} consecutive '
                    f'row IN/OUT pairs. Optionally splice the chain into a base '
                    f'node so the planner can reach it.'
                ).classes('text-xs').style('color:#57606a;max-width:340px')
                base_input = ui.input(
                    label='Splice into',
                    placeholder='leave blank for chain only',
                    value=default_base,
                ).classes('w-full mt-2')
                ui.label(
                    'Without a base node the chain is wired internally but '
                    'remains unreachable from the rest of the graph.'
                ).classes('text-xs').style(
                    'color:#9a6700;max-width:340px;margin-top:4px')
                with ui.row().classes('w-full justify-end gap-2 mt-2'):
                    ui.button('Cancel',
                              on_click=lambda: d.submit('cancel')).props(
                                  'flat no-caps')
                    ui.button('Repair', color='positive',
                              on_click=lambda: d.submit('ok')).props('no-caps')

            result = await d
            if result != 'ok':
                return

            base = (base_input.value or '').strip()
            if base and not topo_doc.has_node(base):
                self.f2c_save_status = f'ERROR: base node {base!r} not in map'
                return
            self.repair_row_connectivity(connect_to=base or None)

        repair_btn.on_click(do_repair)

        _save_prev = ['']
        def _refresh_save_status():
            cur = self.f2c_save_status
            if cur == _save_prev[0]:
                return
            _save_prev[0] = cur
            f2c_save_lbl.set_text(cur)
            f2c_save_lbl.style(
                'color:#cf222e' if cur.startswith('ERROR') else
                'color:#1a7f37' if cur else 'color:#57606a')
        ui.timer(0.4, _refresh_save_status)

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
                    if not mission_queue:
                        # This is a protected method, we should probably find a better way
                        # of bubbling up the error.
                        self._mission_store._set_status('ERROR: queue is empty')  # pylint: disable=protected-access
                        return
                    topo_doc = self._topo_doc
                    topo_nodes = topo_doc.nodes if topo_doc is not None else []
                    rows_for_store = [
                        next(
                            (nd.name for nd in topo_nodes
                             if nd.meta.get('row_id') == rid
                             and nd.meta.get('row_role') == 'entry'),
                            f'ROW_{rid}_IN',
                        )
                        for rid, _, _ in mission_queue
                    ]
                    # All steps in the queue share the action+params of the
                    # first entry.  Mixed-action missions aren't supported in
                    # the store yet — first step wins.
                    first_act    = mission_queue[0][1]
                    first_params = mission_queue[0][2]
                    rpt = int(save_repeat.value) if save_repeat.value else None
                    self._mission_store.add(
                        rows=rows_for_store,
                        action=first_act,
                        action_params=first_params,
                        name=save_name_input.value or '',
                        repeat_every_hours=rpt,
                        active=True,
                    )

                ui.button('Save', on_click=_save_mission).props(
                    'color=primary no-caps dense')

            mission_store_status = ui.label('').classes('text-xs font-mono mt-1').style(
                'color:#57606a')

            _mstore_prev = [-1]
            def _refresh_missions():
                # Store status line
                cur_status = self.mission_status
                mission_store_status.set_text(cur_status)
                mission_store_status.style(
                    'color:#cf222e' if cur_status.startswith('ERROR') else
                    'color:#1a7f37' if cur_status else 'color:#57606a')

                v = self.missions_version
                if v == _mstore_prev[0]:
                    return
                _mstore_prev[0] = v

                snap = self.missions
                missions_col.clear()
                missions_empty_lbl.set_visibility(not snap)
                run_btn.set_enabled(not self._mission_running)

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
                        due_h = self._mission_store.next_due_in_hours(mid)
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
                                lambda e, m=mid: self._mission_store.set_active(m, e.value))
                            ui.button(
                                '✕',
                                on_click=lambda _, m=mid: self._mission_store.delete(m),
                            ).props('flat dense').classes('text-xs').style('color:#cf222e')

            ui.timer(0.5, _refresh_missions)

        # OBSTACLE: obstacle list + map rendering, full width below the queue.
        attach_mission_obstacle_panel(draw_handle)

# ─────────────────────────────────────────────────────────────────────────────
# NiceGuiNode mission executor methods
# ─────────────────────────────────────────────────────────────────────────────

    def _get_tool_publisher(self, topic: str, is_float: bool = False):
        """Return (creating if needed) a publisher for the given tool topic.
        is_float=True → std_msgs/Float64; False → std_msgs/Bool."""
        key = (topic, is_float)
        if key not in self._tool_publishers:
            if is_float:
                self._tool_publishers[key] = self._ros.create_publisher(Float64, topic, 1)
            else:
                self._tool_publishers[key] = self._ros.create_publisher(Bool, topic, 1)
        return self._tool_publishers[key]

    def _publish_tool_msgs(self, action_key: str,
                           action_params: dict | None,
                           enable: bool) -> None:
        """Publish all (topic, value) pairs from action_ros_msgs."""
        for topic, value in action_ros_msgs(action_key, action_params, enable):
            try:
                if isinstance(value, bool):
                    pub = self._get_tool_publisher(topic, is_float=False)
                    msg = Bool()
                    msg.data = value
                else:
                    pub = self._get_tool_publisher(topic, is_float=True)
                    msg = Float64()
                    if not isinstance(value, (int, float, str)):
                        raise TypeError(f'unsupported tool value type: {type(value)!r}')
                    msg.data = float(value)
                pub.publish(msg)
            except Exception as exc:
                self.get_logger().warn(
                    f'_publish_tool_msgs({topic}, enable={enable}): {exc}')

    def _run_mission(self, queue: list, status_lbl) -> None:
        """
        Start executing the queued row mission.

        Parameters:
            queue (list): Tuples containing a row ID, action key, and action parameters.
            status_lbl: UI status label updated with mission progress and outcome.
        """
        if not queue:
            status_lbl.set_text('ERROR: queue is empty')
            status_lbl.style('color:#cf222e')
            return
        if self._mission_running:
            status_lbl.set_text('ERROR: mission already running')
            status_lbl.style('color:#cf222e')
            return
        if not self._run_vm.navigation_available:
            status_lbl.set_text('ERROR: action client unavailable')
            status_lbl.style('color:#cf222e')
            return
        if not self._topo_doc:
            status_lbl.set_text('ERROR: no topology map loaded')
            status_lbl.style('color:#cf222e')
            return

        # Resolve row_id → (entry_node, exit_node) from current topo map.
        row_entry: dict[int, str] = {}
        row_exit:  dict[int, str] = {}
        for nd in self._topo_doc.nodes:
            meta = nd.meta
            rid  = meta.get('row_id')
            role = meta.get('row_role', '')
            if rid is None:
                continue
            try:
                rid_int = int(rid)
            except (TypeError, ValueError):
                continue
            if role == 'entry':
                row_entry[rid_int] = nd.name
            elif role == 'exit':
                row_exit[rid_int] = nd.name

        missing_entry = [rid for rid, _, _ in queue if rid not in row_entry]
        missing_exit  = [rid for rid, _, _ in queue if rid not in row_exit]
        if missing_entry or missing_exit:
            missing = sorted(set(missing_entry) | set(missing_exit))
            status_lbl.set_text(f'ERROR: incomplete row nodes for row(s) {missing}')
            status_lbl.style('color:#cf222e')
            return

        steps = [(rid, row_entry[rid], row_exit[rid], act, params)
                 for rid, act, params in queue]

        self._mission_running = True
        self._mission_cancel  = False
        status_lbl.set_text(f'Starting {len(steps)} step(s)…')
        status_lbl.style('color:#57606a')

        def _execute():
            """
            Execute the queued mission steps and update the mission status.

            Each step navigates to its entry node with the implement disabled, then
            traverses to its exit node with the configured action enabled. Stops on
            cancellation, soft-estop activation, or navigation failure, and resets the
            mission state when execution ends.
            """
            success_overall = True
            for step_idx, (rid, entry_node, exit_node, action, params) in enumerate(steps):
                if self._mission_cancel or self._global_vm.soft_estop_active:
                    status_lbl.set_text('Cancelled')
                    status_lbl.style('color:#9a6700')
                    success_overall = False
                    break

                adef = ACTIONS.get(action)
                label = f'{adef.icon} {adef.label}' if adef else action

                # Leg 1: transit to entry — implement OFF, this isn't the row yet.
                status_lbl.set_text(
                    f'[{step_idx+1}/{len(steps)}] Row {rid} → transit to {entry_node}')
                status_lbl.style('color:#0969da')
                nav_ok = self._send_goal_sync(entry_node)
                if not nav_ok:
                    status_lbl.set_text(
                        f'Row {rid}: transit to {entry_node} failed — stopping mission')
                    status_lbl.style('color:#cf222e')
                    success_overall = False
                    break

                if self._mission_cancel or self._global_vm.soft_estop_active:
                    status_lbl.set_text('Cancelled')
                    status_lbl.style('color:#9a6700')
                    success_overall = False
                    break

                # Leg 2: entry -> exit — implement ON, this is the row itself.
                status_lbl.set_text(
                    f'[{step_idx+1}/{len(steps)}] Row {rid} {label} → {exit_node}')
                status_lbl.style('color:#0969da')

                self._publish_tool_msgs(action, params, enable=True)
                nav_ok = self._send_goal_sync(exit_node)
                self._publish_tool_msgs(action, params, enable=False)

                if not nav_ok:
                    status_lbl.set_text(
                        f'Row {rid}: traversal to {exit_node} failed — stopping mission')
                    status_lbl.style('color:#cf222e')
                    success_overall = False
                    break

            if success_overall:
                status_lbl.set_text(
                    f'Mission complete — {len(steps)} step(s) done ✓')
                status_lbl.style('color:#1a7f37')

            self._mission_running = False
            self._mission_cancel  = False

        threading.Thread(target=_execute, daemon=True).start()

    def _send_goal_sync(self, target: str, timeout_sec: float = 300.0) -> bool:
        """Navigate to a topology node and block until it ends.

        Returns:
            bool: True if the robot arrived, False if navigation failed, was cancelled, timed
            out, or the action server was unavailable.
        """
        return self._run_vm.navigate_and_wait(
            target, timeout_sec,
            lambda: self._mission_cancel or self._global_vm.soft_estop_active)

    def cancel_mission(self) -> None:
        """Signal the executor thread to stop after the current row."""
        self._mission_cancel = True
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

    def save_map_as(self, name: str) -> str:
        """Save a named copy of the current map. See TopologyApplicationService.save_map_as."""
        return self._topo_app_service.save_map_as(name)

    def archive_and_clear_map(self) -> str:
        """Archive the saved map as <name>_<N> and replace it with an empty default map.

        Reset row driving to geometry mode on success. Return an archive status, or an
        'ERROR:' status for a missing map or a read/write failure; the row mode is left
        alone in that case.
        """
        if not self._topo_doc:
            return 'ERROR: no map loaded'
        try:
            archive = self._topo_app_service.archive_and_clear()
        except Exception as e:
            self.get_logger().error(f'archive_and_clear_map failed: {e}')
            return f'ERROR: {e}'
        self._row_action = ROW_ACTION
        self._run_vm.drop_node.row_action = ROW_ACTION
        self.get_logger().info(f'archive_and_clear_map: archived to {archive}')
        return f'archived → {archive}'

    # ── System tab ────────────────────────────────────────────────────────────

    def _system_content(self) -> None:
        """Build the System tab interface for telemetry, safety monitoring, GPS, simulation tools,
        plant configuration, and map management.
        """
        with ui.row().classes('items-stretch w-full gap-3'):
            with ui.card().classes('flex-1'):
                ui.label('Telemetry').classes('font-semibold mb-2')
                ui.html('<div class="sec-label">Linear velocity</div>')
                ui.slider(min=-1, max=1, step=0.05, value=0).props(
                    'readonly selection-color=transparent color=green').bind_value_from(
                        self._telemetry_vm, 'linear_velocity')
                ui.html('<div class="sec-label mt-2">Angular velocity</div>')
                ui.slider(min=-1, max=1, step=0.05, value=0).props(
                    'readonly selection-color=transparent color=green').bind_value_from(
                        self._telemetry_vm, 'angular_velocity')
                ui.html('<div class="sec-label mt-3">Battery</div>')
                ui.label().classes('text-sm').bind_text_from(
                    self._telemetry_vm, 'battery_text')
            with ui.card().classes('flex-1'):
                ui.label('Safety').classes('font-semibold mb-2')
                ui.html('<div class="sec-label">Bumpers</div>')
                for attr, label in [('bumper_front_top_active', 'Front top'),
                                    ('bumper_front_bottom_active', 'Front bottom'),
                                    ('bumper_back_active', 'Rear')]:
                    with ui.row().classes('items-center gap-0'):
                        dot = ui.html('<span class="dot-off"></span>')
                        ui.label(label).classes('text-sm')
                    def _mk(d=dot, a=attr):
                        """Return a callback updating HTML element d from telemetry attribute a."""
                        def _u():
                            """Refresh the bumper indicator from its current telemetry flag."""
                            d.set_content(
                                f'<span class="dot-{"warn" if getattr(self._telemetry_vm, a) else "ok"}"></span>')
                        return _u
                    ui.timer(0.2, _mk())
                ui.html('<div class="sec-label mt-3">E-stops</div>')
                for attr, label in [('estop_front_active', 'Front'), ('estop_back_active', 'Rear')]:
                    with ui.row().classes('items-center gap-0'):
                        dot = ui.html('<span class="dot-off"></span>')
                        ui.label(label).classes('text-sm')
                    def _mk2(d=dot, a=attr):
                        """Return a callback updating HTML element d from telemetry attribute a."""
                        def _u():
                            """Refresh the hardware e-stop indicator from its current telemetry flag."""
                            d.set_content(
                                f'<span class="dot-{"warn" if getattr(self._telemetry_vm, a) else "off"}"></span>')
                        return _u
                    ui.timer(0.2, _mk2())
        with ui.card().classes('w-full mt-3'):
            ui.label('ESP').classes('font-semibold mb-2')
            with ui.row().classes('gap-2 flex-wrap'):
                ui.button('Enable',
                    on_click=self._robot_brain_app_service.enable).props(
                        'color=positive outline no-caps').classes('px-4')
                ui.button('Disable',
                    on_click=self._robot_brain_app_service.disable).props(
                        'color=negative outline no-caps').classes('px-4')
                ui.button('Reset',
                    on_click=self._robot_brain_app_service.reset).props(
                        'color=warning outline no-caps').classes('px-4')
                ui.button('Restart',
                    on_click=self._robot_brain_app_service.restart).props(
                        'color=primary outline no-caps').classes('px-4')
                ui.button('Configure',
                    on_click=self._robot_brain_app_service.configure).props(
                        'outline no-caps').classes('px-4')
        with ui.card().classes('w-full mt-3'):
            ui.label('GPS').classes('font-semibold mb-2')
            leaflet = ui.leaflet(center=FIELD27_CENTER, zoom=18).classes('w-full h-80')
            leaflet.run_map_method(
                'fitBounds', [list(FIELD27_BOUNDS[0]), list(FIELD27_BOUNDS[1])])
            marker  = leaflet.marker(latlng=leaflet.center)
            gps_status_lbl = ui.label('—').classes('text-xs font-mono mt-1').style('color:#57606a')
            _FIX_LABELS = {-1: 'NO FIX', 0: 'AUTONOMOUS', 1: 'SBAS',
                            2: 'DGNSS', 4: 'RTK FLOAT', 5: 'RTK FIXED'}
            def update_gps_ui():
                """Refresh the map marker and GPS details, keeping the display when no message exists."""
                gps = self._telemetry_vm.gps
                if gps is not None:
                    lat, lon = gps.latitude, gps.longitude
                    leaflet.set_center((lat, lon))
                    marker.move(lat, lon)
                    code = gps.status.status
                    cov  = gps.position_covariance[0]
                    gps_status_lbl.set_text(
                        f'{_FIX_LABELS.get(code, str(code))}  '
                        f'{lat:.6f}, {lon:.6f}  '
                        f'alt={gps.altitude:.1f}m  '
                        f'σ={cov**0.5:.2f}m')
                    col = '#1a7f37' if code == 5 else '#9a6700' if code >= 1 else '#cf222e'
                    gps_status_lbl.style(f'color:{col}')
            ui.timer(2.0, update_gps_ui)
        with ui.card().classes('w-full mt-3'):
            ui.label('Tools').classes('font-semibold mb-2')
            with ui.row().classes('items-center gap-3 flex-wrap'):
                _host = ui.context.client.request.url.hostname or 'localhost'
                if ':' in _host:
                    _host = f'[{_host}]'
                _host = escape(_host, quote=True)

                # ── Graph Explorer ───────────────────────────────────────
                _explorer_proc: list = _shared('explorer', lambda: [None])
                _explorer_lbl = ui.label('').classes('text-xs font-mono').style('color:#57606a')
                _restore_label(_explorer_lbl, _explorer_proc[0])
                def _start_explorer():
                    if _alive(_explorer_proc[0]):
                        _explorer_lbl.set_text('already running')
                        return
                    try:
                        _explorer_proc[0] = _spawn_logged(
                            ['ros2', 'run', 'ros2graph_explorer', 'ros2graph_explorer'],
                            '/tmp/ros2graph_explorer.log')
                        _explorer_lbl.set_text(
                            f'started (pid {_explorer_proc[0].pid}) '
                            '- log: /tmp/ros2graph_explorer.log')
                        _explorer_lbl.style('color:#1a7f37')
                        _report_if_exited(_explorer_proc[0], _explorer_lbl,
                                          '/tmp/ros2graph_explorer.log')
                    except Exception as exc:
                        _explorer_lbl.set_text(f'ERROR: {exc}')
                        _explorer_lbl.style('color:#cf222e')
                async def _stop_explorer():
                    await ng_run.io_bound(_kill_group, _explorer_proc[0])
                    _explorer_proc[0] = None
                    _explorer_lbl.set_text('stopped')
                    _explorer_lbl.style('color:#57606a')
                ui.button('Start Graph Explorer', on_click=_start_explorer).props(
                    'outline no-caps').classes('px-4')
                ui.button('Stop', on_click=_stop_explorer).props(
                    'outline no-caps').classes('px-4')
                ui.html(
                    f'<a href="http://{_host}:8734/" target="_blank" '
                    'style="font-size:13px;color:var(--blue);text-decoration:none;'
                    'padding:6px 12px;border:1px solid var(--blue);border-radius:4px;'
                    'font-family:\'Courier New\',monospace;">'
                    '↗ Graph Explorer</a>'
                )
                ui.html(
                    '<a href="https://github.com/nilseuropa/ros2graph_explorer#build--launch"'
                    ' target="_blank"'
                    ' style="font-size:11px;color:var(--txt-muted);text-decoration:none;'
                    'font-family:\'Courier New\',monospace;">'
                    '📄 Documentation</a>'
                )

                ui.separator().classes('w-full my-1')

                # ── ros2grapher ──────────────────────────────────────────
                _grapher_proc: list = _shared('grapher', lambda: [None])
                _grapher_lbl = ui.label('').classes('text-xs font-mono').style('color:#57606a')
                _restore_label(_grapher_lbl, _grapher_proc[0])
                def _start_grapher():
                    if _alive(_grapher_proc[0]):
                        _grapher_lbl.set_text('already running')
                        return
                    try:
                        # ros2grapher serves its output directory over plain HTTP on
                        # all interfaces. Give it a private directory holding only
                        # index.html so it cannot expose the workspace (.env etc).
                        os.makedirs(_GRAPHER_DIR, exist_ok=True)
                        _grapher_proc[0] = _spawn_logged(
                            ['ros2grapher', '/workspace', '-o', f'{_GRAPHER_DIR}/index.html',
                             '--port', '8888'],
                            '/tmp/ros2grapher.log', cwd=_GRAPHER_DIR)
                        _grapher_lbl.set_text(
                            f'started (pid {_grapher_proc[0].pid}) - open http://{_host}:8888 '
                            f'(scan takes a few seconds) - log: /tmp/ros2grapher.log')
                        _grapher_lbl.style('color:#1a7f37')
                        _report_if_exited(_grapher_proc[0], _grapher_lbl, '/tmp/ros2grapher.log')
                    except Exception as exc:
                        _grapher_lbl.set_text(f'ERROR: {exc}')
                        _grapher_lbl.style('color:#cf222e')
                async def _stop_grapher():
                    await ng_run.io_bound(_kill_group, _grapher_proc[0])
                    _grapher_proc[0] = None
                    _grapher_lbl.set_text('stopped')
                    _grapher_lbl.style('color:#57606a')
                ui.button('Start ros2grapher', on_click=_start_grapher).props(
                    'outline no-caps').classes('px-4')
                ui.button('Stop', on_click=_stop_grapher).props(
                    'outline no-caps').classes('px-4')
                ui.html(
                    f'<a href="http://{_host}:8888/" target="_blank" '
                    'style="font-size:13px;color:var(--blue);text-decoration:none;'
                    'padding:6px 12px;border:1px solid var(--blue);border-radius:4px;'
                    'font-family:\'Courier New\',monospace;">'
                    '↗ ros2grapher</a>'
                )
                ui.html(
                    '<a href="https://github.com/Supull/ros2grapher"'
                    ' target="_blank"'
                    ' style="font-size:11px;color:var(--txt-muted);text-decoration:none;'
                    'font-family:\'Courier New\',monospace;">'
                    '📄 Documentation</a>'
                )

                ui.separator().classes('w-full my-1')

                # ── RViz ─────────────────────────────────────────────────
                _rviz_proc: list = _shared('rviz', lambda: [None])
                _rviz_daemons: list = _shared('rviz_daemons', list)
                _rviz_lbl = ui.label('').classes('text-xs font-mono').style('color:#57606a')
                _restore_label(_rviz_lbl, _rviz_proc[0])

                async def _start_rviz():
                    if _alive(_rviz_proc[0]):
                        _rviz_lbl.set_text('already running')
                        return
                    try:
                        # Resolve topo_nav RViz config (has MarkerArray displays
                        # pre-wired to /topological_map_visualisation et al).
                        # Falls back to no config if topo_nav isn't installed.
                        try:
                            rviz_cfg = os.path.join(
                                get_package_share_directory('topological_navigation'),
                                'rviz', 'topological_navigation.rviz',
                            )
                        except PackageNotFoundError:
                            rviz_cfg = None
                        await ng_run.io_bound(_start_vnc_stack, ':98', 5901, 6081, _rviz_daemons)
                        rviz_args = ['ros2', 'run', 'rviz2', 'rviz2']
                        if rviz_cfg is not None:
                            rviz_args += ['-d', rviz_cfg]
                        _rviz_proc[0] = _spawn_logged(
                            rviz_args, '/tmp/rviz.log', env={**os.environ, 'DISPLAY': ':98'})
                        suffix = '' if rviz_cfg else ' (no topo config found)'
                        _rviz_lbl.set_text(
                            f'started (pid {_rviz_proc[0].pid}){suffix} - log: /tmp/rviz.log')
                        _rviz_lbl.style('color:#1a7f37')
                        _report_if_exited(_rviz_proc[0], _rviz_lbl, '/tmp/rviz.log')
                    except Exception as exc:
                        _rviz_lbl.set_text(f'ERROR: {exc}')
                        _rviz_lbl.style('color:#cf222e')

                async def _stop_rviz():
                    await ng_run.io_bound(_kill_group, _rviz_proc[0])
                    _rviz_proc[0] = None
                    await ng_run.io_bound(_stop_all, _rviz_daemons)
                    _rviz_lbl.set_text('stopped')
                    _rviz_lbl.style('color:#57606a')

                ui.button('Launch RViz', on_click=_start_rviz).props(
                    'outline no-caps').classes('px-4')
                ui.button('Stop RViz', on_click=_stop_rviz).props(
                    'outline no-caps').classes('px-4')
                ui.html(
                    f'<a href="http://{_host}:6081/vnc.html" target="_blank" '
                    'style="font-size:13px;color:var(--blue);text-decoration:none;'
                    'padding:6px 12px;border:1px solid var(--blue);border-radius:4px;'
                    'font-family:\'Courier New\',monospace;">'
                    '↗ RViz (noVNC)</a>'
                )
                ui.separator().classes('w-full my-1')

                # ── Medkit Gateway ───────────────────────────────────────
                _medkit_proc: list = [None]
                _medkit_stopping = False
                _medkit_lbl = ui.label('').classes('text-xs font-mono').style('color:#57606a')
                def _start_medkit():
                    """Launch Medkit on loopback unless this tab's process is running.

                    Display process creation errors in the status label. A successful
                    start reports the launch PID without checking gateway readiness.
                    """
                    if _medkit_stopping:
                        return
                    if _medkit_proc[0] is not None and _medkit_proc[0].poll() is None:
                        _medkit_lbl.set_text('already running')
                        return
                    try:
                        _medkit_proc[0] = subprocess.Popen(
                            ['ros2', 'launch', 'ros2_medkit_gateway', 'bringup.launch.py',
                             'server_host:=127.0.0.1'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        _medkit_lbl.set_text(f'started (pid {_medkit_proc[0].pid})')
                        _medkit_lbl.style('color:#1a7f37')
                    except Exception as exc:
                        _medkit_lbl.set_text(f'ERROR: {exc}')
                        _medkit_lbl.style('color:#cf222e')
                async def _stop_medkit():
                    """Reap the launch process before releasing its handle, off the UI loop."""
                    nonlocal _medkit_stopping
                    if _medkit_stopping:
                        return
                    _medkit_stopping = True
                    try:
                        if _medkit_proc[0] is not None:
                            _medkit_lbl.set_text('stopping')
                            await ng_run.io_bound(_shutdown_medkit, _medkit_proc[0])
                            _medkit_proc[0] = None
                        _medkit_lbl.set_text('stopped')
                        _medkit_lbl.style('color:#57606a')
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        _medkit_lbl.set_text(f'ERROR: {exc}')
                        _medkit_lbl.style('color:#cf222e')
                    finally:
                        _medkit_stopping = False

                def _shutdown_medkit(proc):
                    """Allow ROS launch to stop its children before escalating on timeout."""
                    proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=5)
                ui.button('Start Medkit Gateway', on_click=_start_medkit).props(
                    'outline no-caps').classes('px-4')
                ui.button('Stop Medkit Gateway', on_click=_stop_medkit).props(
                    'outline no-caps').classes('px-4')
                _medkit_host = ui.context.client.request.url.hostname or 'localhost'
                if ':' in _medkit_host:
                    _medkit_host = f'[{_medkit_host}]'
                _medkit_host = escape(_medkit_host, quote=True)
                ui.html(
                    f'<a href="http://{_medkit_host}:8080/" target="_blank" '
                    'style="font-size:13px;color:var(--blue);text-decoration:none;'
                    'padding:6px 12px;border:1px solid var(--blue);border-radius:4px;'
                    'font-family:\'Courier New\',monospace;">'
                    f'↗ Gateway ({_medkit_host}:8080)</a>'
                )
                ui.html(
                    '<div style="font-size:11px;color:var(--txt-muted);'
                    'font-family:\'Courier New\',monospace;line-height:1.5;">'
                    'SOVD web UI runs on the host, not in this container. On host:<br>'
                    '<code>docker run -p 3000:80 ghcr.io/selfpatch/sovd_web_ui:latest</code><br>'
                    f'Then open <a href="http://{_medkit_host}:3000/" target="_blank" '
                    f'style="color:var(--blue);text-decoration:none;">{_medkit_host}:3000</a> '
                    f'and set its gateway URL to <code>http://{_medkit_host}:8080</code>.<br>'
                    'The gateway starts on loopback for host-local use. Remote access requires '
                    'authentication, TLS, and restricted network access.'
                    '</div>'
                )

                ui.separator().classes('w-full my-1')

                # ── Gazebo Sim ───────────────────────────────────────────
                _gazebo_proc: list = _shared('gazebo', lambda: [None])
                _spawn_proc: list = _shared('gazebo_spawn', lambda: [None])
                _gazebo_daemons: list = _shared('gazebo_daemons', list)  # Xvfb, x11vnc, websockify
                _gazebo_lbl = ui.label('').classes('text-xs font-mono').style('color:#57606a')
                _restore_label(_gazebo_lbl, _gazebo_proc[0])

                # Robot model selector — controls which xacro is spawned and
                # which urdf arg is passed to sowbot_sim.launch.py.
                # sowbot_01:       TrackedVehicle + TrackController (DART required)
                # robo_caatinga:   DiffDrive skid-steer (ODE or DART both fine)
                # ifarmate:        DiffDrive skid-steer (plugin wiring copied from caatinga)
                _ROBOT_MODELS = {
                    'sowbot (tracked)':      'sowbot_01.xacro',
                    'caatinga (diff drive)': 'robo_caatinga.urdf.xacro',
                    'ifarmate (diff drive)': 'ifarmate.urdf.xacro',
                }
                _robot_model: dict = {'xacro': 'sowbot_01.xacro'}

                with ui.row().classes('items-center gap-3 mb-1'):
                    ui.html('<span style="font-size:12px;color:#57606a;'
                            'font-family:monospace">Robot model</span>')
                    ui.toggle(
                        list(_ROBOT_MODELS.keys()),
                        value='sowbot (tracked)',
                        on_change=lambda e: _robot_model.update(
                            xacro=_ROBOT_MODELS[e.value]),
                    ).props('dense')

                _SIM_ENV = {
                    **os.environ,
                    'TMAP2_FILE': '/workspace/maps/maize_map',
                    'GZ_SIM_RESOURCE_PATH': (
                        # Forest3D-generated models (model://ground,
                        # model://crop/plant) live here — must be first or
                        # gz sim aborts world load with "Unable to find uri".
                        '/workspace/models'
                        ':/workspace/install/virtual_maize_field'
                        '/share/virtual_maize_field/models'
                        + (':' + os.environ['GZ_SIM_RESOURCE_PATH']
                           if os.environ.get('GZ_SIM_RESOURCE_PATH') else '')
                    ),
                }
                def _sim_cmd(headless: bool = False) -> list:
                    """Build the sowbot_sim launch command using the current robot model."""
                    return [
                        'ros2', 'launch', 'devkit_bringup', 'sowbot_sim.launch.py',
                        'world:=maize.world',
                        f'urdf:={_robot_model["xacro"]}',
                        f'headless:={"true" if headless else "false"}',
                    ]

                async def _start_gazebo_browser():
                    if _gazebo_proc[0] is not None and _gazebo_proc[0].poll() is None:
                        _gazebo_lbl.set_text('already running')
                        return
                    try:
                        # We don't use `with` for these because we save the process arguments and
                        # manage them manually.
                        # Spawn Xvfb on :99 only if not already taken
                        await ng_run.io_bound(_start_vnc_stack, ':99', 5900, 6080, _gazebo_daemons)
                        # Xvfb has no DRI/GLX driver, so it can't honour
                        # whatever hardware-render env manage.py set for the
                        # container (nvidia __GLX_VENDOR_LIBRARY_NAME, or
                        # /dev/dri passthrough). Force llvmpipe software GL
                        # for this process only, matching docs/research/Sim.md
                        #
                        # Env vars alone are NOT enough for gz-sim: RViz's
                        # Ogre1/GLX renderer honours LIBGL_ALWAYS_SOFTWARE
                        # directly, but gz-sim's Ogre2 initialises via
                        # EGL_EXT_platform_device, which explicitly enumerates
                        # /dev/dri and selects a real GPU node — Mesa's
                        # software-force guard refuses to override an
                        # explicitly-selected hardware device (this is the
                        # "Not allowed to force software rendering..." warning
                        # immediately before the segfault in gazebo_sim.log).
                        # No EGL env var changes that once a real render node
                        # is visible, and this container's /dev:/dev +
                        # privileged mode means it always is.
                        #
                        # Confirmed fix (tested manually in-container): hide
                        # /dev/dri from just this subprocess with a private
                        # mount namespace — the same trick gz-sim's own CI
                        # uses on GPU-less runners. Only this child's view of
                        # /dev is masked; nothing else in the container.
                        env = {
                            **_SIM_ENV,
                            'DISPLAY': ':99',
                            'LIBGL_ALWAYS_SOFTWARE': '1',
                            'GALLIUM_DRIVER': 'llvmpipe',
                            'MESA_LOADER_DRIVER_OVERRIDE': 'llvmpipe',
                        }
                        _quoted_cmd = ' '.join(
                            "'" + a.replace("'", "'\\''") + "'" for a in _sim_cmd())
                        wrapped_cmd = [
                            'unshare', '--mount', '--propagation', 'private',
                            '--', 'bash', '-c',
                            f'mount -t tmpfs tmpfs /dev/dri 2>/dev/null; '
                            f'exec {_quoted_cmd}',
                        ]
                        # Log to a file so a crashed launch is diagnosable:
                        # tail /tmp/gazebo_sim.log.
                        _gazebo_proc[0] = _spawn_logged(wrapped_cmd, '/tmp/gazebo_sim.log', env=env)
                        _gazebo_lbl.set_text(
                            f'browser mode — {_robot_model["xacro"]} — pid {_gazebo_proc[0].pid}')
                        _gazebo_lbl.style('color:#1a7f37')
                    except Exception as exc:
                        _gazebo_lbl.set_text(f'ERROR: {exc}')
                        _gazebo_lbl.style('color:#cf222e')

                async def _stop_gazebo():
                    for proc_var in (_gazebo_proc, _spawn_proc):
                        await ng_run.io_bound(_kill_group, proc_var[0])
                        proc_var[0] = None
                    await ng_run.io_bound(_stop_all, _gazebo_daemons)
                    _gazebo_lbl.set_text('stopped')
                    _gazebo_lbl.style('color:#57606a')

                # Rebuild maize.world FROM the saved topo map: plants are
                # studded in the inter-row gaps of the R*_IN/OUT nodes. Gazebo
                # reads the world only at launch, so a running sim must be
                # stopped and relaunched to see a rebuild — we refuse mid-run
                # rather than silently no-op. Uses the shared worldgen.sh
                # (also called by Launch Sim) so Rebuild and Launch Sim agree
                # on the cache key and never regenerate each other's world.
                _MAP_FILE   = '/workspace/maps/maize_map'

                async def _rebuild_world():
                    if _gazebo_proc[0] is not None and _gazebo_proc[0].poll() is None:
                        _gazebo_lbl.set_text(
                            'stop the sim before rebuilding — Gazebo reads the '
                            'world only at launch')
                        _gazebo_lbl.style('color:#cf222e')
                        return
                    if not os.path.exists(_MAP_FILE):
                        _gazebo_lbl.set_text(
                            f'no saved map at {_MAP_FILE} — drop/save nodes first')
                        _gazebo_lbl.style('color:#cf222e')
                        return
                    try:
                        _gazebo_lbl.set_text('rebuilding world from map…')
                        _gazebo_lbl.style('color:#57606a')
                        # Get plant placement values (cm → m conversion)
                        spacing_m = float(plant_spacing.value or 100) / 100.0
                        row_w_m = float(row_width_input.value or 80) / 100.0
                        scale = float(plant_scale.value or 100) / 100.0
                        cat = scale_category.value or 'all'
                        model = plant_model.value or 'plant'
                        weed_density = (
                            int(weed_density_scale.value)
                            if weed_density_scale.value is not None else 10)
                        # Validate selected model exists on disk
                        model_dir = _CROP_MODELS_DIR / model
                        if not model_dir.is_dir() or not (model_dir / 'model.sdf').is_file():
                            _gazebo_lbl.set_text(
                                f'model "{model}" not found — upload it first')
                            _gazebo_lbl.style('color:#cf222e')
                            return
                        r = await ng_run.io_bound(
                            subprocess.run,
                            ['bash', '/workspace/worldgen.sh',
                             '--plant-spacing', str(spacing_m),
                             '--row-width', str(row_w_m),
                             '--plant-scale', str(scale),
                             '--scale-category', cat,
                             '--crop-model', model,
                             '--weed-density', str(weed_density)],
                            capture_output=True, text=True, timeout=120,
                            check=False
                        )
                        if r is None:
                            _gazebo_lbl.set_text('rebuild cancelled')
                            _gazebo_lbl.style('color:#cf222e')
                            return
                        if r.returncode != 0:
                            err = (r.stderr or r.stdout or 'unknown error').strip()
                            _gazebo_lbl.set_text(f'rebuild failed: {err[-200:]}')
                            _gazebo_lbl.style('color:#cf222e')
                            return
                        m = re.search(r'Weed density:\s*(\d+)', r.stdout or '')
                        weed_count = m.group(1) if m else '?'
                        summary = (f'world rebuilt (spacing={spacing_m:.2f}m, '
                                  f'row={row_w_m:.2f}m, '
                                  f'scale={scale:.2f} on {cat}, '
                                  f'weed density={weed_density}% '
                                  f'({weed_count} weeds), '
                                  f'model={model})')
                        _gazebo_lbl.set_text(f'{summary} — relaunch to view')
                        _gazebo_lbl.style('color:#1a7f37')
                    except subprocess.TimeoutExpired:
                        _gazebo_lbl.set_text('rebuild timed out')
                        _gazebo_lbl.style('color:#cf222e')
                    except Exception as exc:
                        _gazebo_lbl.set_text(f'ERROR: {exc}')
                        _gazebo_lbl.style('color:#cf222e')

                # Hardcoded install prefix — avoids shelling out to
                # `ros2 pkg prefix` which fails when AMENT_PREFIX_PATH
                # is not set in the UI node's subprocess environment.
                _AGRO_PKG = '/workspace/install/devkit_simulation/share/devkit_simulation'

                def _launch_sim(headless: bool = False):
                    """Launch the selected simulation, optionally headless, and update its status.
                    """
                    # Single button, runs the exact same thing as the CLI:
                    # `ros2 launch devkit_bringup sowbot_sim.launch.py
                    #   world:=maize.world urdf:=<selected xacro>`
                    # (same command _sim_cmd() builds for the browser button).
                    # Replaces the old Launch World / Spawn Robot split, which
                    # ran sim.launch.py + nav2_only.launch.py instead — that
                    # path skipped preflight_pkill, fusioncore, and
                    # kill_bootstrap_tfs entirely (all of which only exist in
                    # sowbot_sim.launch.py), causing stale wall-time bootstrap
                    # TFs to fight the real sim-time TF forever and FusionCore
                    # to never run at all. Do not reintroduce that split.
                    if _gazebo_proc[0] is not None and _gazebo_proc[0].poll() is None:
                        _gazebo_lbl.set_text('already running')
                        return
                    try:
                        _gazebo_proc[0] = _spawn_logged(
                            _sim_cmd(headless), '/tmp/gazebo_sim.log', env=_SIM_ENV)
                        _gazebo_lbl.set_text(
                            f'sim launching{" (headless)" if headless else ""} — '
                            f'{_robot_model["xacro"]} — pid {_gazebo_proc[0].pid}')
                        _gazebo_lbl.style('color:#1a7f37')
                    except Exception as exc:
                        _gazebo_lbl.set_text(f'ERROR: {exc}')
                        _gazebo_lbl.style('color:#cf222e')

                # ── Plant Placement Controls ─────────────────────────────
                # Configure plant spacing, weed density row width, and model before
                # rebuilding. Values are stored locally; --plant-spacing and
                # --row-width are passed to topo_to_forest3d.py on rebuild.
                # Passed as --plant-scale, --weed-density --scale-category, and --crop-model.
                _CROP_MODELS_DIR = Path('/workspace/models/crop')

                def _refresh_crop_models():
                    """List valid crop model subfolders (must have model.sdf)."""
                    models = []
                    if _CROP_MODELS_DIR.exists():
                        for d in sorted(_CROP_MODELS_DIR.iterdir()):
                            if d.is_dir() and (d / 'model.sdf').exists():
                                models.append(d.name)
                    return models if models else ['plant']

                ui.separator().classes('w-full my-2')
                ui.html('<span class="sec-label">Plant Placement</span>')

                # Row 1: Plant Spacing | Row Width
                with ui.row().classes('w-full gap-4 mt-1'):
                    with ui.column().classes('flex-1 gap-0'):
                        ui.html('<div class="sec-label">Plant Spacing</div>')
                        plant_spacing = ui.number(
                            value=100, min=10, max=1000, step=5, precision=0,
                            suffix='cm'
                        ).classes('w-full')
                    with ui.column().classes('flex-1 gap-0'):
                        ui.html('<div class="sec-label">Row width</div>')
                        row_width_input = ui.number(
                            value=80, min=20, max=300, step=5, precision=0,
                            suffix='cm'
                        ).classes('w-full')

                spacing_warn_lbl = ui.label('').classes('text-xs').style('color:#9a6700')

                def _check_spacing_warning():
                    rw = float(row_width_input.value or 80)
                    ps = float(plant_spacing.value or 100)
                    if rw >= ps:
                        spacing_warn_lbl.set_text('Warning: row width >= plant spacing')
                    else:
                        spacing_warn_lbl.set_text('')

                row_width_input.on('update:model-value', lambda e: _check_spacing_warning())
                plant_spacing.on('update:model-value', lambda e: _check_spacing_warning())

                # Row 2: Scale + category | Model selector
                with ui.row().classes('w-full gap-4 mt-1'):
                    with ui.column().classes('flex-1 gap-0'):
                        ui.html('<div class="sec-label">Scale</div>')
                        with ui.row().classes('items-center gap-1 w-full'):
                            plant_scale = ui.number(
                                value=100, min=5, max=1000, step=5, precision=0,
                                suffix='%'
                            ).classes('flex-1')
                            ui.label('on').classes('text-xs').style('color:#8c959f')
                            scale_category = ui.select(
                                options=['all', 'crop', 'weed', 'irrigation'],
                                value='all'
                            ).classes('w-28')
                    with ui.column().classes('flex-1 gap-0'):
                        ui.html('<div class="sec-label">Model</div>')
                        plant_model = ui.select(
                            options=_refresh_crop_models(),
                            value=_refresh_crop_models()[0] if _refresh_crop_models() else None
                        ).classes('w-full')

                # Row 3: weed_density + category (coming)
                with ui.row().classes('w-full gap-4 mt-1'):
                    with ui.column().classes('flex-1 gap-0'):
                        ui.html('<div class="sec-label">Weed Density</div>')
                        with ui.row().classes('items-center gap-1 w-full'):
                            weed_density_scale = ui.number(
                                value=10, min=0, max=100, step=5, precision=0,
                                suffix='%'
                            ).classes('flex-1')
                            ui.label('on').classes('text-xs').style('color:#8c959f')

                # Upload section
                ui.html('<div class="sec-label mt-2">Upload new model</div>')
                _visual_mesh_data: dict = {'name': None, 'data': None}
                _collision_mesh_data: dict = {'name': None, 'data': None}
                _plant_upload_lbl = ui.label('').classes('text-xs font-mono').style(
                    'color:#57606a')

                model_name_input = ui.input(
                    label='Model name',
                    placeholder='my_plant',
                ).classes('w-40')

                def _is_valid_gltf(data, fname):
                    ext = fname.lower().rsplit('.', 1)[-1] if '.' in fname else ''
                    if ext == 'glb':
                        return data[:4] == b'glTF'
                    if ext == 'gltf':
                        return data.strip()[:1] in (b'{', b'[')
                    return False

                async def _handle_visual_upload(e):
                    try:
                        if hasattr(e, 'file'):
                            data = await e.file.read()
                            fname = e.file.name if hasattr(e.file, 'name') else 'visual.glb'
                        else:
                            data = e.content.read()
                            fname = getattr(e, 'name', 'visual.glb')
                    except Exception as exc:
                        _plant_upload_lbl.set_text(f'upload failed: {exc}')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    # Validate extension
                    if not fname.lower().endswith(('.glb', '.gltf')):
                        _plant_upload_lbl.set_text('only .glb/.gltf files accepted')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    # Validate magic bytes
                    if not _is_valid_gltf(data, fname):
                        _plant_upload_lbl.set_text('invalid glTF file')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    # Validate size (50MB cap)
                    if len(data) > 50 * 1024 * 1024:
                        _plant_upload_lbl.set_text('file too large (max 50MB)')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    _visual_mesh_data['name'] = fname
                    _visual_mesh_data['data'] = data
                    _plant_upload_lbl.set_text(f'visual: {fname} ({len(data)//1024}KB)')
                    _plant_upload_lbl.style('color:#1a7f37')

                async def _handle_collision_upload(e):
                    try:
                        if hasattr(e, 'file'):
                            data = await e.file.read()
                            fname = e.file.name if hasattr(e.file, 'name') else 'collision.glb'
                        else:
                            data = e.content.read()
                            fname = getattr(e, 'name', 'collision.glb')
                    except Exception as exc:
                        _plant_upload_lbl.set_text(f'collision upload failed: {exc}')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    if not fname.lower().endswith(('.glb', '.gltf')):
                        _plant_upload_lbl.set_text('only .glb/.gltf files accepted')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    if not _is_valid_gltf(data, fname):
                        _plant_upload_lbl.set_text('invalid glTF file')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    if len(data) > 50 * 1024 * 1024:
                        _plant_upload_lbl.set_text('collision file too large (max 50MB)')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    _collision_mesh_data['name'] = fname
                    _collision_mesh_data['data'] = data
                    _plant_upload_lbl.set_text(
                        f'visual: {_visual_mesh_data["name"] or "—"}, '
                        f'collision: {fname}')
                    _plant_upload_lbl.style('color:#1a7f37')

                async def _create_plant_model():
                    name = (model_name_input.value or '').strip()
                    if not name:
                        _plant_upload_lbl.set_text('enter a model name')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    if not re.match(r'^[a-zA-Z0-9_]+$', name):
                        _plant_upload_lbl.set_text('name must be alphanumeric + underscore')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    model_dir = _CROP_MODELS_DIR / name
                    if model_dir.exists():
                        _plant_upload_lbl.set_text(f'model "{name}" already exists')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    if _visual_mesh_data['data'] is None:
                        _plant_upload_lbl.set_text('upload a visual mesh first')
                        _plant_upload_lbl.style('color:#cf222e')
                        return
                    try:
                        mesh_dir = model_dir / 'mesh'
                        mesh_dir.mkdir(parents=True, exist_ok=True)
                        # Write visual mesh
                        visual_fname = f'{name}.glb'
                        with open(mesh_dir / visual_fname, 'wb') as f:
                            f.write(_visual_mesh_data['data'])
                        # Write collision mesh (or reuse visual)
                        if _collision_mesh_data['data'] is not None:
                            collision_fname = f'{name}_collision.glb'
                            with open(mesh_dir / collision_fname, 'wb') as f:
                                f.write(_collision_mesh_data['data'])
                        else:
                            collision_fname = visual_fname
                        # Write model.config
                        model_config = f'''<?xml version="1.0"?>
<model>
    <name>{name}</name>
    <version>1.0</version>
    <sdf version="1.8">model.sdf</sdf>
    <author>
        <name>User Upload</name>
    </author>
    <description>{name} plant model</description>
</model>
'''
                        with open(model_dir / 'model.config', 'w', encoding='utf-8') as f:
                            f.write(model_config)
                        # Write model.sdf
                        model_sdf = f'''<?xml version="1.0" ?>
<sdf version="1.8">
    <model name="{name}">
        <static>true</static>
        <link name="link">
            <collision name="collision">
                <geometry>
                    <mesh><uri>mesh/{collision_fname}</uri><scale>1 1 1</scale></mesh>
                </geometry>
            </collision>
            <visual name="visual">
                <geometry>
                    <mesh><uri>mesh/{visual_fname}</uri><scale>1 1 1</scale></mesh>
                </geometry>
            </visual>
        </link>
    </model>
</sdf>
'''
                        with open(model_dir / 'model.sdf', 'w', encoding='utf-8') as f:
                            f.write(model_sdf)
                        # Refresh dropdown
                        new_models = _refresh_crop_models()
                        plant_model.options = new_models
                        plant_model.value = name
                        # Clear upload state
                        _visual_mesh_data['name'] = None
                        _visual_mesh_data['data'] = None
                        _collision_mesh_data['name'] = None
                        _collision_mesh_data['data'] = None
                        model_name_input.value = ''
                        _plant_upload_lbl.set_text(f'model "{name}" created')
                        _plant_upload_lbl.style('color:#1a7f37')
                    except Exception as exc:
                        _plant_upload_lbl.set_text(f'failed: {exc}')
                        _plant_upload_lbl.style('color:#cf222e')

                with ui.row().classes('items-center gap-2 flex-wrap'):
                    ui.upload(
                        label='Visual mesh (.glb/.gltf) *',
                        auto_upload=True,
                        on_upload=_handle_visual_upload,
                    ).props('accept=.glb,.gltf').classes('max-w-xs')
                    ui.upload(
                        label='Collision mesh (optional)',
                        auto_upload=True,
                        on_upload=_handle_collision_upload,
                    ).props('accept=.glb,.gltf').classes('max-w-xs')
                    ui.button('Create Model', on_click=_create_plant_model).props(
                        'color=primary no-caps').classes('px-4')

                ui.separator().classes('w-full my-2')

                with ui.row().classes('items-center gap-2 flex-wrap'):
                    ui.button('Launch Sim', on_click=_launch_sim).props(
                        'color=positive no-caps').classes('px-4 font-bold')
                    ui.button('Rebuild World from Map', on_click=_rebuild_world).props(
                        'outline no-caps').classes('px-4')
                    ui.button('Launch Sim (headless)',
                              on_click=lambda: _launch_sim(headless=True)).props(
                        'outline no-caps').classes('px-4')
                    ui.button('Launch Sim (browser)', on_click=_start_gazebo_browser).props(
                        'outline no-caps').classes('px-4')
                    ui.button('Stop Sim', on_click=_stop_gazebo).props(
                        'outline no-caps').classes('px-4')
                    ui.html(
                        f'<a href="http://{_host}:6080/vnc.html" target="_blank" '
                        'style="font-size:13px;color:var(--blue);text-decoration:none;'
                        'padding:6px 12px;border:1px solid var(--blue);border-radius:4px;'
                        'font-family:\'Courier New\',monospace;">'
                        '↗ Gazebo (noVNC)</a>'
                    )


                # ── Sowbot Row Follow (sim) ──────────────────────────────
                # Launches neo.launch.py in sim mode: subscribes to the
                # Gazebo-bridged /camera/image_raw instead of opening a
                # V4L2 device, runs the TSM detector, and publishes
                # /cmd_vel via crop_row_node. limbic_row_follow_node
                # (started by sim_nav.launch.py) calls /row_follow/enable
                # on this process when topo nav reaches an _IN node.
                ui.separator().classes('w-full my-1')
                _neo_proc: list = _shared('neo', lambda: [None])
                _neo_lbl = ui.label('').classes('text-xs font-mono').style('color:#57606a')
                _restore_label(_neo_lbl, _neo_proc[0])

                def _start_neo():
                    if _neo_proc[0] is not None and _neo_proc[0].poll() is None:
                        _neo_lbl.set_text('already running')
                        return
                    try:
                        _neo_proc[0] = _spawn_logged(
                            [
                                'ros2', 'launch', 'devkit_bringup', 'neo.launch.py',
                                'use_camera:=false',
                                'detector:=tsm',
                                'image_topic:=/camera/image_raw',
                            ],
                            '/tmp/neo_sim.log', env=os.environ.copy())
                        _neo_lbl.set_text(
                            f'running — pid {_neo_proc[0].pid} · log: /tmp/neo_sim.log')
                        _neo_lbl.style('color:#1a7f37')
                    except Exception as exc:
                        _neo_lbl.set_text(f'ERROR: {exc}')
                        _neo_lbl.style('color:#cf222e')

                async def _stop_neo():
                    if _neo_proc[0] is None:
                        _neo_lbl.set_text('not running')
                        return
                    await ng_run.io_bound(_kill_group, _neo_proc[0])
                    _neo_proc[0] = None
                    _neo_lbl.set_text('stopped')
                    _neo_lbl.style('color:#57606a')

                with ui.row().classes('items-center gap-2 flex-wrap'):
                    ui.html(
                        '<span class=\"sec-label\" style=\"white-space:nowrap\">'
                        'Sowbot Row Follow</span>')
                    ui.button('Start', on_click=_start_neo).props(
                        'color=positive no-caps').classes('px-4')
                    ui.button('Stop',  on_click=_stop_neo).props(
                        'color=negative outline no-caps').classes('px-4')

                # ── Soil texture import ──────────────────────────────────
                # Import a soil asset folder (zipped): its image maps are
                # harvested into /workspace/uploads (persisted across image
                # rebuilds) and staged into the ground model on the next world
                # rebuild, where Forest3D turns them into a PBR material.
                _SOIL_TEX_DIR = Path('/workspace/uploads/soil_custom/textures')
                _soil_lbl = ui.label('').classes('text-xs font-mono').style('color:#57606a')

                def _classify_map(name):
                    # Mirror Forest3D's filename-keyword classification so the
                    # label previews what the PBR material will use.
                    nl = name.lower()
                    if any(k in nl for k in ('diff', 'albedo', 'base', 'color')):
                        return 'albedo'
                    if any(k in nl for k in ('normal', 'nor', 'nrm')):
                        return 'normal'
                    if 'rough' in nl:
                        return 'roughness'
                    return 'other'

                def _harvest_soil_zip(data):
                    # Sync worker (runs off the event loop via io_bound): extract
                    # gz-loadable image maps from the zip bytes into _SOIL_TEX_DIR.
                    # Returns the staged basenames; raises BadZipFile / ValueError.
                    zf = zipfile.ZipFile(io.BytesIO(data))
                    # Forest3D skips .exr, so only harvest gz-loadable images.
                    members = [m for m in zf.namelist()
                               if not m.endswith('/')
                               and Path(m).suffix.lower() in ('.jpg', '.jpeg', '.png')]
                    if not members:
                        raise ValueError(
                            'no .jpg/.png maps found in the zip '
                            '(textures may be .exr — convert first)')
                    # Replace any previous import so exactly one soil set is
                    # active; flatten folder structure to basenames.
                    if _SOIL_TEX_DIR.exists():
                        shutil.rmtree(_SOIL_TEX_DIR)
                    _SOIL_TEX_DIR.mkdir(parents=True, exist_ok=True)
                    names = []
                    for m in members:
                        out = _SOIL_TEX_DIR / Path(m).name
                        with zf.open(m) as src, open(out, 'wb') as fh:
                            shutil.copyfileobj(src, fh)
                        names.append(out.name)
                    return names

                async def _import_soil_zip(e):
                    # NiceGUI changed the upload event shape across versions:
                    # newer exposes e.file (FileUpload, async read()); older
                    # exposed e.content (a sync file-like object).
                    try:
                        if hasattr(e, 'file'):
                            data = await e.file.read()
                        else:
                            data = e.content.read()
                    except Exception as exc:
                        _soil_lbl.set_text(f'import failed: {exc}')
                        _soil_lbl.style('color:#cf222e')
                        return
                    try:
                        names = await ng_run.io_bound(_harvest_soil_zip, data)
                    except zipfile.BadZipFile:
                        _soil_lbl.set_text('not a valid .zip file')
                        _soil_lbl.style('color:#cf222e')
                        return
                    except ValueError as exc:
                        _soil_lbl.set_text(str(exc))
                        _soil_lbl.style('color:#cf222e')
                        return
                    except Exception as exc:
                        _soil_lbl.set_text(f'import failed: {exc}')
                        _soil_lbl.style('color:#cf222e')
                        return
                    if names is None:
                        _soil_lbl.set_text('import cancelled')
                        _soil_lbl.style('color:#cf222e')
                        return
                    summary = ', '.join(f'{_classify_map(n)}={n}' for n in names)
                    _soil_lbl.set_text(
                        f'imported {len(names)} map(s) — {summary}. '
                        'Rebuild World to apply.')
                    _soil_lbl.style('color:#1a7f37')

                with ui.row().classes('items-center gap-2 flex-wrap'):
                    ui.upload(
                        label='Import soil asset (.zip)',
                        auto_upload=True,
                        on_upload=_import_soil_zip,
                    ).props('accept=.zip').classes('max-w-md')

        with ui.card().classes('w-full mt-3'):
            ui.label('Map Archive').classes('font-semibold mb-2')
            archive_lbl = ui.label('').classes('text-xs font-mono mt-1').style(
                'color:#57606a')

            with ui.row().classes('items-center gap-2 w-full mb-2'):
                save_name = ui.input(
                    label='Save copy as', placeholder='e.g. north_field_2026',
                ).props('dense').classes('flex-1')

                def _do_save():
                    """Save a named map copy, show its status, and clear the name on success."""
                    status = self.save_map_as(save_name.value)
                    archive_lbl.set_text(status)
                    archive_lbl.style(
                        'color:#cf222e' if status.startswith('ERROR')
                        else 'color:#1a7f37')
                    if not status.startswith('ERROR'):
                        save_name.set_value('')

                ui.button('Save', on_click=_do_save).props('outline no-caps')

            async def _do_archive():
                map_name = self._topo_doc.name if self._topo_doc else '?'
                with ui.dialog() as dlg, ui.card():
                    ui.label('Archive and clear map').classes('font-semibold')
                    ui.label(
                        f'Copies "{map_name}" to "{map_name}_N" then wipes all '
                        f'nodes from the live map. Cannot be undone from the UI.'
                    ).classes('text-xs').style('color:#57606a;max-width:340px')
                    with ui.row().classes('w-full justify-end gap-2 mt-3'):
                        ui.button('Cancel',
                                  on_click=lambda: dlg.submit('cancel')).props(
                                      'flat no-caps')
                        ui.button('Archive & Clear', color='negative',
                                  on_click=lambda: dlg.submit('ok')).props('no-caps')

                result = await dlg
                if result != 'ok':
                    return
                status = self.archive_and_clear_map()
                archive_lbl.set_text(status)
                archive_lbl.style(
                    'color:#cf222e' if status.startswith('ERROR')
                    else 'color:#1a7f37')

            ui.button('Archive & Clear Map', on_click=_do_archive).props(
                'color=negative outline no-caps').classes('px-4')

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
