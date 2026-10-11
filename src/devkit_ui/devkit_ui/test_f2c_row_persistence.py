"""Save and repair contracts at the UI's row-builder boundary."""

# The existing harness supplies the private state normally initialized by ROS.
# pylint: disable=no-member,protected-access
import copy
import unittest
from types import SimpleNamespace

from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.test_f2c_row_metadata import make_node, patch_row_geometry
from devkit_ui.test_topo_row_builder import targets

SWATHS = [[(51.0, -2.0), (51.5, -1.5)]]


class TestSaveValidation(unittest.TestCase):
    def setUp(self) -> None:
        patch_row_geometry(self)

    def test_missing_inputs_report_errors_without_persisting(self) -> None:
        cases = (
            ({'_f2c_swaths': []}, 'no rows planned'),
            ({'_is_sim': False}, 'no odometry'),
            ({'latest_gps': None}, 'no GPS fix yet'),
            ({'_topo_doc': None}, 'map not loaded'),
            ({'_f2c_origin_ll': None}, 'no F2C field origin'),
            ({'_f2c_swaths': [[], [(51.0, -2.0)]]}, 'nothing added'),
        )
        for overrides, error in cases:
            with self.subTest(error=error):
                node = make_node(SWATHS)
                for name, value in overrides.items():
                    setattr(node, name, value)
                before = copy.deepcopy(node._topo_doc.to_dict()) if node._topo_doc else None

                node.save_f2c_rows_to_topo('crop', 1)

                self.assertTrue(node.f2c_save_status.startswith('ERROR:'))
                self.assertIn(error, node.f2c_save_status)
                node._persist_and_reload.assert_not_called()
                if node._topo_doc:
                    self.assertEqual(node._topo_doc.to_dict(), before)

    def test_empty_or_sanitized_away_prefix_never_writes(self) -> None:
        for prefix in (None, '', '  ', '-.!'):
            with self.subTest(prefix=prefix):
                node = make_node(SWATHS)

                node.save_f2c_rows_to_topo(prefix, 1)

                self.assertEqual(node.f2c_save_status, 'ERROR: prefix required')
                node._persist_and_reload.assert_not_called()
                self.assertEqual(list(node._topo_doc.nodes), [])

    def test_nonfinite_or_zero_gps_coordinates_never_write(self) -> None:
        cases = [(0.0, 0.0), (5e-10, -5e-10)]
        for value in (float('nan'), float('inf'), float('-inf')):
            cases.extend(((value, -2.0), (51.0, value)))
        for lat, lon in cases:
            with self.subTest(lat=lat, lon=lon):
                node = make_node(SWATHS)
                node.latest_gps.latitude = lat
                node.latest_gps.longitude = lon

                node.save_f2c_rows_to_topo('crop', 1)

                self.assertTrue(node.f2c_save_status.startswith('ERROR: GPS lat/lon'))
                node._persist_and_reload.assert_not_called()
                self.assertEqual(list(node._topo_doc.nodes), [])

    def test_a_single_zero_coordinate_is_a_valid_fix(self) -> None:
        for lat, lon in ((0.0, -2.0), (51.0, 0.0)):
            with self.subTest(lat=lat, lon=lon):
                node = make_node(SWATHS)
                node.latest_gps.latitude = lat
                node.latest_gps.longitude = lon

                node.save_f2c_rows_to_topo('crop', 1)

                node._persist_and_reload.assert_called_once()
                self.assertTrue(node.f2c_save_status.startswith('saved 1 rows'))

    def test_negative_fix_status_warns_but_preserves_usable_coordinates(self) -> None:
        node = make_node(SWATHS)
        node.latest_gps.status.status = -1

        node.save_f2c_rows_to_topo('crop', 1)

        node.get_logger().warn.assert_called_once()
        self.assertIn('proceeding with status=-1', node.get_logger().warn.call_args.args[0])
        node._persist_and_reload.assert_called_once()
        for saved in node._topo_doc.nodes:
            self.assertEqual(saved.to_dict()['node']['properties']['gps_fix_type'], -1)


class TestSavePersistence(unittest.TestCase):
    def setUp(self) -> None:
        patch_row_geometry(self)

    def test_simulation_uses_field_origin_even_when_gps_and_odometry_differ(self) -> None:
        node = make_node(SWATHS)
        node.latest_gps.latitude = 40.0
        node.latest_gps.longitude = 10.0
        node.latest_odom = odometry(100.0, 200.0)

        node.save_f2c_rows_to_topo('crop', 1)

        entry = node._topo_doc.get_node('CROP_R1_IN')
        exit_node = node._topo_doc.get_node('CROP_R1_OUT')
        self.assertEqual((entry.x, entry.y), (0.0, 0.0))
        self.assertEqual((exit_node.x, exit_node.y), (0.5, 0.5))

    def test_hardware_uses_gps_and_odometry_without_field_origin(self) -> None:
        node = make_node(SWATHS)
        node._is_sim = False
        node._f2c_origin_ll = None
        node.latest_gps.latitude = 50.0
        node.latest_gps.longitude = -3.0
        node.latest_odom = odometry(12.0, -8.0)

        node.save_f2c_rows_to_topo('crop', 1)

        entry = node._topo_doc.get_node('CROP_R1_IN')
        exit_node = node._topo_doc.get_node('CROP_R1_OUT')
        self.assertEqual((entry.x, entry.y), (13.0, -7.0))
        self.assertEqual((exit_node.x, exit_node.y), (13.5, -6.5))

    def test_save_callback_updates_supplied_document_with_normalized_prefix_and_frame(self) -> None:
        node = make_node(SWATHS)
        node._topo_doc = TopoDoc(name='survey', transformation={'topo_frame_id': 'survey_frame'})
        node._persist_and_reload.side_effect = None

        node.save_f2c_rows_to_topo('  crop - a!  ', 7, overwrite=True)

        self.assertEqual(list(node._topo_doc.nodes), [])
        node._persist_and_reload.assert_called_once()
        modify, owner, attr, success = node._persist_and_reload.call_args.args
        self.assertIs(owner, node)
        self.assertEqual(attr, 'f2c_save_status')
        self.assertEqual(node.f2c_save_status, 'writing 1 rows · CROP__A · standalone…')
        self.assertEqual(success, 'saved 1 rows · CROP__A · standalone')
        disk_doc = TopoDoc(name='survey', nodes=[
            TopoNode(name='CROP__A_R99_IN', x=1, y=1), TopoNode(name='KEEP', x=2, y=2),
        ])

        modify(disk_doc)

        self.assertEqual({n.name for n in disk_doc.nodes}, {
            'KEEP', 'CROP__A_R7_IN', 'CROP__A_R7_OUT',
        })
        for name in ('CROP__A_R7_IN', 'CROP__A_R7_OUT'):
            saved = disk_doc.get_node(name).to_dict()
            self.assertEqual(saved['node']['nav_frame'], 'survey_frame')
            self.assertEqual(saved['meta']['map'], 'survey')

    def test_empty_map_name_and_frame_use_defaults(self) -> None:
        node = make_node(SWATHS)
        node._topo_doc = TopoDoc(name='', transformation={'topo_frame_id': ''})

        node.save_f2c_rows_to_topo('crop', 1)

        entry = node._topo_doc.get_node('CROP_R1_IN').to_dict()
        self.assertEqual(entry['node']['nav_frame'], 'map')
        self.assertEqual(entry['meta']['map'], 'mixed_test_map')

    def test_splice_prefers_current_node_then_valid_selection(self) -> None:
        cases = [('CURRENT', 'SELECTED', 'CURRENT'), ('missing', 'SELECTED', 'SELECTED')]
        cases.extend((value, 'SELECTED', 'SELECTED') for value in ('—', 'none', 'None', '', None))
        cases.append(('missing', 'absent', None))
        for current, selected, expected in cases:
            with self.subTest(current=current, selected=selected):
                node = make_node(SWATHS)
                for name in ('CURRENT', 'SELECTED'):
                    node._topo_doc.insert_node(TopoNode(name=name, x=0, y=0))
                node._topo_vm.current_node = current
                node._topo_vm.selected_node = selected

                node.save_f2c_rows_to_topo('crop', 1)

                expected_edges = {expected: NAV_ACTION} if expected else {}
                self.assertEqual(targets(node._topo_doc.get_node('CROP_R1_OUT')), expected_edges)
                self.assertEqual(targets(node._topo_doc.get_node('CROP_R1_IN')), {
                    'CROP_R1_OUT': node._row_action, **expected_edges,
                })
                for name in ('CURRENT', 'SELECTED'):
                    self.assertEqual(list(node._topo_doc.get_node(name).edges), [])
                suffix = f'spliced @ {expected}' if expected else 'standalone'
                self.assertTrue(node.f2c_save_status.endswith(suffix))


class TestRepairPersistence(unittest.TestCase):
    def test_missing_map_or_rows_never_persists(self) -> None:
        for doc, error in ((None, 'map not loaded'), (TopoDoc(name='field'), 'no row nodes found')):
            with self.subTest(error=error):
                node = make_node([])
                node._topo_doc = doc

                node.repair_row_connectivity()

                self.assertEqual(node.f2c_save_status, f'ERROR: {error}')
                node._persist_and_reload.assert_not_called()

    def test_repair_updates_memory_and_replays_edges_on_separate_persisted_document(self) -> None:
        node = make_node([])
        node._row_action = 'limbic_row_follow'
        for name, role, y in (('IN', 'entry', 0), ('W1', 'waypoint', 5), ('OUT', 'exit', 10)):
            node._topo_doc.insert_node(TopoNode(
                name=name, x=0, y=y, meta={'row_id': 1, 'row_role': role}))
        disk_doc = copy.deepcopy(node._topo_doc)
        disk_doc.get_node('IN').add_edge('W1', action='authored')
        node._persist_and_reload.side_effect = None

        node.repair_row_connectivity(connect_to='missing')

        self.assertEqual(targets(node._topo_doc.get_node('IN')), {'W1': 'limbic_row_follow'})
        self.assertEqual(targets(node._topo_doc.get_node('W1')), {'OUT': 'limbic_row_follow'})
        self.assertEqual(node.f2c_save_status, 'repair: adding 2 edges…')
        node.get_logger().warn.assert_called_once_with(
            "repair: connect_to='missing' not in map, ignoring")
        node._persist_and_reload.assert_called_once()
        modify, owner, attr, success = node._persist_and_reload.call_args.args
        self.assertIs(owner, node)
        self.assertEqual(attr, 'f2c_save_status')
        self.assertEqual(success, 'repair: wired 2 edges — NO SPLICE, chain still isolated')

        modify(disk_doc)

        self.assertEqual(targets(disk_doc.get_node('IN')), {'W1': 'authored'})
        self.assertEqual(targets(disk_doc.get_node('W1')), {'OUT': 'limbic_row_follow'})
        before = copy.deepcopy(disk_doc.to_dict())
        modify(disk_doc)
        self.assertEqual(disk_doc.to_dict(), before)

    def test_lone_endpoint_warns_and_does_not_schedule_empty_repair(self) -> None:
        node = make_node([])
        node._topo_doc.insert_node(TopoNode(
            name='IN', x=0, y=0, meta={'row_id': 7, 'row_role': 'entry'}))

        node.repair_row_connectivity()

        self.assertEqual(node.f2c_save_status, 'repair: already wired (1 rows)')
        node.get_logger().warn.assert_called_once()
        self.assertIn('skipping headland edges', node.get_logger().warn.call_args.args[0])
        node._persist_and_reload.assert_not_called()


def odometry(x: float, y: float) -> SimpleNamespace:
    """Supply just the pose fields consumed by the save boundary."""
    return SimpleNamespace(pose=SimpleNamespace(pose=SimpleNamespace(
        position=SimpleNamespace(x=x, y=y))))


if __name__ == '__main__':
    unittest.main()
