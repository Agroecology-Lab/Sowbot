"""The Run page composed over fake UI elements and real view models."""
# pylint: disable=protected-access,no-member,attribute-defined-outside-init
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Imported before the patched block so the cards and every other test module share one registry.
from devkit_ui import process_registry  # noqa: F401  # pylint: disable=unused-import
from devkit_ui.application_services.telemetry_application_service import (
    TelemetryApplicationService,
)
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.test_page_composition import page_nicegui
from devkit_ui.test_run_ui import fake_ui
from devkit_ui.view_models.global_view_model import GlobalViewModel
from devkit_ui.view_models.run_view_model import RunViewModel
from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel
from devkit_ui.view_models.topology_view_model import TopologyViewModel

_modules_before = set(sys.modules)
with patch.dict(sys.modules, {'nicegui': page_nicegui}):
    from devkit_ui.pages.run import nav_page
    from devkit_ui.pages.run.nav_page import NavPage
    _imported_here = set(sys.modules) - _modules_before

# Leaving the block discards these modules, but a surviving parent package keeps them as
# attributes, and a later `from package import module` would then return the stale copy.
for _name in _imported_here:
    _parent, _, _child = _name.rpartition('.')
    if _parent in sys.modules and hasattr(sys.modules[_parent], _child):
        delattr(sys.modules[_parent], _child)

_COLLABORATORS = (
    'JoystickControlCard', 'NodeMapCard', 'TrackCard', 'DropNodeCard', 'RowDiscoveryCard',
    'NavigationSidebar', 'attach_nav_card', 'confirm_delete_node',
)


class TestNavPage(unittest.TestCase):
    def setUp(self):
        """Compose the page over shared view models and synthetic telemetry."""
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.ui_on = self.enterContext(patch.object(fake_ui, 'on', create=True))
        self.mocks = {name: self.enterContext(patch.object(nav_page, name, new=Mock()))
                      for name in _COLLABORATORS}
        self.drive = Mock(soft_estop_active=False)
        self.global_vm = GlobalViewModel(self.drive)
        self.run_vm = RunViewModel(self.drive)
        self.run_vm.go_to = Mock()
        self.run_vm.cancel_navigation = Mock()
        self.topo_vm = TopologyViewModel(Mock())
        self.topo_vm.update_doc(TopoDoc(name='field', nodes=[
            TopoNode(name='A', x=0.0, y=0.0), TopoNode(name='B', x=2.0, y=3.0),
        ]))
        self.telemetry = SimpleNamespace(
            latest_gps=None,
            latest_odom=SimpleNamespace(
                pose=SimpleNamespace(
                    pose=SimpleNamespace(position=SimpleNamespace(x=1.0, y=2.0))),
                twist=SimpleNamespace(twist=SimpleNamespace(
                    linear=SimpleNamespace(x=0.3), angular=SimpleNamespace(z=-0.4))),
            ),
            robot_pose=Mock(return_value=(1.0, 2.0, 0.5)),
        )
        self.telemetry_vm = TelemetryViewModel(TelemetryApplicationService(self.telemetry))
        self.obstacle_vm = Mock()
        self.authoring = Mock()
        self.page = NavPage(self.global_vm, self.run_vm, self.topo_vm, self.telemetry_vm,
                            self.obstacle_vm, self.authoring)
        self.refresh = fake_ui.timers[0].callback
        self.sidebar = self.mocks['NavigationSidebar'].return_value

    def test_page_is_a_full_width_column(self):
        """Verify the page itself is styled as a full-width column."""
        self.assertEqual(self.page.class_names, 'w-full gap-0')

    def test_cards_receive_shared_view_models_and_authoring_actions(self):
        """Verify each card is wired to the shared state and the authoring view model."""
        self.mocks['NodeMapCard'].assert_called_once_with(
            topo_vm=self.topo_vm, pose_state=self.run_vm.node_map)
        self.mocks['JoystickControlCard'].assert_called_once_with(
            global_vm=self.global_vm, run_vm=self.run_vm)
        self.mocks['TrackCard'].assert_called_once_with(
            state=self.run_vm.track, on_start=self.authoring.start_track,
            on_stop=self.authoring.stop_track)
        self.mocks['DropNodeCard'].assert_called_once_with(
            state=self.run_vm.drop_node, topo_vm=self.topo_vm,
            on_drop=self.authoring.drop_node, on_row_action=self.authoring.set_row_action)
        self.mocks['RowDiscoveryCard'].assert_called_once_with(
            state=self.run_vm.discovery, on_start=self.run_vm.start_discovery,
            on_stop=self.run_vm.stop_discovery)
        self.mocks['attach_nav_card'].assert_called_once_with(self.obstacle_vm)

    def test_browser_disconnect_releases_the_joystick(self):
        """Verify the page registers the joystick release for a dropped browser connection."""
        self.assertEqual(fake_ui.context.disconnect_handlers, [self.run_vm.release_joystick])

    def test_drive_commands_do_not_overwrite_measured_velocity(self):
        """Verify drive commands go to the drive service and leave measured velocity alone."""
        self.telemetry_vm.linear_velocity = 0.2
        self.telemetry_vm.angular_velocity = -0.3

        self.run_vm.move_joystick(0.8, 0.6)
        self.global_vm.toggle_estop()
        self.drive.soft_estop_active = True

        self.drive.move_joystick.assert_called_once_with(0.8, 0.6)
        self.drive.toggle_estop.assert_called_once_with(False)
        self.assertTrue(self.global_vm.soft_estop_active)
        self.assertEqual((self.telemetry_vm.linear_velocity, self.telemetry_vm.angular_velocity),
                         (0.2, -0.3))

    def test_refresh_uses_odometry_velocity_and_forwards_latest_pose(self):
        """Verify refresh updates telemetry without rebuilding an unchanged node list."""
        self.refresh()
        self.assertEqual((self.telemetry_vm.linear_velocity, self.telemetry_vm.angular_velocity),
                         (0.3, -0.4))
        self.assertEqual(self.run_vm.joystick.pose_lbl, '(1.00, 2.00)')
        self.assertEqual(self.run_vm.node_map.robot_pose, (1.0, 2.0, 0.5))
        self.sidebar.render_nodes.assert_called_once()

        self.telemetry.robot_pose.return_value = (1.01, 2.01, 0.501)
        self.refresh()

        self.assertEqual(self.run_vm.node_map.robot_pose, (1.01, 2.01, 0.501))
        self.sidebar.render_nodes.assert_called_once()

    def test_refresh_rebuilds_the_node_list_when_selection_or_nodes_change(self):
        """Verify a new selection or node set re-renders the sidebar list."""
        self.refresh()
        self.topo_vm.set_selected_node('B')
        self.refresh()
        self.assertEqual(self.sidebar.render_nodes.call_count, 2)
        self.assertEqual(self.sidebar.render_nodes.call_args.args[1], 'B')

        self.topo_vm.topo_doc.add_node(TopoNode(name='C', x=5.0, y=5.0))
        self.refresh()
        self.assertEqual(self.sidebar.render_nodes.call_count, 3)

    def test_refresh_without_map_still_updates_telemetry(self):
        """Verify telemetry updates without a map while map-specific refresh work is skipped."""
        self.topo_vm.update_doc(None)

        self.refresh()

        self.assertEqual((self.telemetry_vm.linear_velocity, self.telemetry_vm.angular_velocity),
                         (0.3, -0.4))
        self.assertEqual(self.run_vm.joystick.pose_lbl, '(1.00, 2.00)')
        self.telemetry.robot_pose.assert_not_called()
        self.sidebar.render_nodes.assert_not_called()

    def test_losing_odometry_and_robot_pose_clears_display_state(self):
        """Verify lost telemetry clears the pose label and robot overlay state."""
        self.refresh()
        self.telemetry.latest_odom = None
        self.telemetry.robot_pose.return_value = None

        self.refresh()

        self.assertEqual(self.run_vm.joystick.pose_lbl, 'no odom')
        self.assertIsNone(self.run_vm.node_map.robot_pose)

    def test_node_click_ignores_invalid_payloads_and_selects_existing_node(self):
        """Verify node clicks select existing nodes and tolerate invalid data or a missing map."""
        self.assertEqual(self.ui_on.call_args.args[0], 'topo_node_clicked')
        callback = self.ui_on.call_args.args[1]
        self.topo_vm.set_selected_node('A')
        for args in (None, {}, {'node': ''}, {'node': 'missing'}):
            with self.subTest(args=args):
                callback(SimpleNamespace(args=args))
                self.assertEqual(self.topo_vm.selected_node, 'A')
        callback(SimpleNamespace(args={'node': 'B'}))
        self.assertEqual(self.topo_vm.selected_node, 'B')
        self.topo_vm.update_doc(None)
        callback(SimpleNamespace(args={'node': 'A'}))
        self.assertIsNone(self.topo_vm.selected_node)

    def test_sidebar_actions_use_latest_selection_and_separate_navigation_state(self):
        """Verify sidebar commands use the current selection and distinct navigation state."""
        callbacks = self.mocks['NavigationSidebar'].call_args.kwargs
        self.assertIs(callbacks['topo_vm'], self.topo_vm)
        self.assertIs(callbacks['nav_state'], self.run_vm.topo)
        self.assertIs(callbacks['global_store'], self.global_vm)
        callbacks['on_go']()
        self.run_vm.go_to.assert_not_called()

        callbacks['on_select']('B')
        callbacks['on_go']()
        callbacks['on_delete']()
        callbacks['on_cancel']()

        self.run_vm.go_to.assert_called_once_with('B', False)
        self.mocks['confirm_delete_node'].assert_called_once_with(
            self.topo_vm, 'B', self.authoring.delete_node)
        self.run_vm.cancel_navigation.assert_called_once_with()

    def test_go_passes_the_live_soft_estop_state(self):
        """Verify Go reads the soft e-stop when pressed, not when the page was built."""
        go = self.mocks['NavigationSidebar'].call_args.kwargs['on_go']
        self.topo_vm.set_selected_node('A')
        self.drive.soft_estop_active = True

        go()

        self.run_vm.go_to.assert_called_once_with('A', True)
