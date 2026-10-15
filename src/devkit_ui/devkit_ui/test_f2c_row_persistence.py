"""Save and repair contracts at the UI's row-builder boundary."""

# The existing harness supplies the private state normally initialized by ROS.
# pylint: disable=no-member,protected-access
import copy
import unittest
from types import SimpleNamespace

from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.test_f2c_row_metadata import SERVICE_LOGGER, make_node, patch_row_geometry
from devkit_ui.test_topo_row_builder import targets

SWATHS = [[(51.0, -2.0), (51.5, -1.5)]]


class TestSaveValidation(unittest.TestCase):
    def setUp(self) -> None:
        """Replace row geometry with deterministic projection and resampling stubs."""
        patch_row_geometry(self)

    def test_missing_inputs_report_errors_without_persisting(self) -> None:
        """Verify missing save inputs report errors without changing or persisting the map."""
        cases = (
            ({'swaths': []}, 'no rows planned'),
            ({'is_sim': False}, 'no odometry'),
            ({'latest_gps': None}, 'no GPS fix yet'),
            ({'doc': None}, 'map not loaded'),
            ({'origin_ll': None}, 'no F2C field origin'),
            ({'swaths': [[], [(51.0, -2.0)]]}, 'nothing added'),
        )
        for overrides, error in cases:
            with self.subTest(error=error):
                node = make_node(SWATHS)
                for name, value in overrides.items():
                    setattr(node, name, value)
                before = copy.deepcopy(node.doc.to_dict()) if node.doc else None

                node.save('crop', 1)

                self.assertTrue(node.save_status.startswith('ERROR:'))
                self.assertIn(error, node.save_status)
                node.topology.persist_and_reload.assert_not_called()
                if node.doc:
                    self.assertEqual(node.doc.to_dict(), before)

    def test_empty_or_sanitized_away_prefix_never_writes(self) -> None:
        """Verify prefixes without usable characters prevent saving rows."""
        for prefix in (None, '', '  ', '-.!'):
            with self.subTest(prefix=prefix):
                node = make_node(SWATHS)

                node.save(prefix, 1)

                self.assertEqual(node.save_status, 'ERROR: prefix required')
                node.topology.persist_and_reload.assert_not_called()
                self.assertEqual(list(node.doc.nodes), [])

    def test_nonfinite_or_zero_gps_coordinates_never_write(self) -> None:
        """Verify nonfinite coordinates and fixes near the origin prevent saving rows."""
        cases = [(0.0, 0.0), (5e-10, -5e-10)]
        for value in (float('nan'), float('inf'), float('-inf')):
            cases.extend(((value, -2.0), (51.0, value)))
        for lat, lon in cases:
            with self.subTest(lat=lat, lon=lon):
                node = make_node(SWATHS)
                node.latest_gps.latitude = lat
                node.latest_gps.longitude = lon

                node.save('crop', 1)

                self.assertTrue(node.save_status.startswith('ERROR: GPS lat/lon'))
                node.topology.persist_and_reload.assert_not_called()
                self.assertEqual(list(node.doc.nodes), [])

    def test_a_single_zero_coordinate_is_a_valid_fix(self) -> None:
        """Verify a zero latitude or longitude alone does not prevent saving rows."""
        for lat, lon in ((0.0, -2.0), (51.0, 0.0)):
            with self.subTest(lat=lat, lon=lon):
                node = make_node(SWATHS)
                node.latest_gps.latitude = lat
                node.latest_gps.longitude = lon

                node.save('crop', 1)

                node.topology.persist_and_reload.assert_called_once()
                self.assertTrue(node.save_status.startswith('saved 1 rows'))

    def test_negative_fix_status_warns_but_preserves_usable_coordinates(self) -> None:
        """Verify a negative GPS status warns but saves usable coordinates with that status."""
        node = make_node(SWATHS)
        node.latest_gps.status.status = -1

        with self.assertLogs(SERVICE_LOGGER, level='WARNING') as logs:
            node.save('crop', 1)

        self.assertEqual(len(logs.records), 1)
        self.assertIn('proceeding with status=-1', logs.records[0].getMessage())
        node.topology.persist_and_reload.assert_called_once()
        for saved in node.doc.nodes:
            self.assertEqual(saved.to_dict()['node']['properties']['gps_fix_type'], -1)


class TestSavePersistence(unittest.TestCase):
    def setUp(self) -> None:
        """Replace row geometry with deterministic projection and resampling stubs."""
        patch_row_geometry(self)

    def test_simulation_uses_field_origin_even_when_gps_and_odometry_differ(self) -> None:
        """Verify simulation anchors rows to the field origin despite other pose inputs."""
        node = make_node(SWATHS)
        node.latest_gps.latitude = 40.0
        node.latest_gps.longitude = 10.0
        node.latest_odom = odometry(100.0, 200.0)

        node.save('crop', 1)

        entry = node.doc.get_node('CROP_R1_IN')
        exit_node = node.doc.get_node('CROP_R1_OUT')
        self.assertEqual((entry.x, entry.y), (0.0, 0.0))
        self.assertEqual((exit_node.x, exit_node.y), (0.5, 0.5))

    def test_hardware_uses_gps_and_odometry_without_field_origin(self) -> None:
        """Verify hardware anchors rows using GPS and odometry without a field origin."""
        node = make_node(SWATHS)
        node.is_sim = False
        node.origin_ll = None
        node.latest_gps.latitude = 50.0
        node.latest_gps.longitude = -3.0
        node.latest_odom = odometry(12.0, -8.0)

        node.save('crop', 1)

        entry = node.doc.get_node('CROP_R1_IN')
        exit_node = node.doc.get_node('CROP_R1_OUT')
        self.assertEqual((entry.x, entry.y), (13.0, -7.0))
        self.assertEqual((exit_node.x, exit_node.y), (13.5, -6.5))

    def test_save_callback_updates_supplied_document_with_normalized_prefix_and_frame(self) -> None:
        """Verify the save callback applies normalized names and the selected map frame."""
        node = make_node(SWATHS)
        node.doc = TopoDoc(name='survey', transformation={'topo_frame_id': 'survey_frame'})
        node.queue_writes()

        node.save('  crop - a!  ', 7, overwrite=True)

        self.assertEqual(list(node.doc.nodes), [])
        node.topology.persist_and_reload.assert_not_called()
        self.assertEqual(node.save_status, 'writing 1 rows · CROP__A · standalone…')
        modify = node.captured_modify()
        node.topology.persist_and_reload.assert_called_once()
        self.assertEqual(node.save_status, 'saved 1 rows · CROP__A · standalone')
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
        """Verify empty map names and frames fall back to the save defaults."""
        node = make_node(SWATHS)
        node.doc = TopoDoc(name='', transformation={'topo_frame_id': ''})

        node.save('crop', 1)

        entry = node.doc.get_node('CROP_R1_IN').to_dict()
        self.assertEqual(entry['node']['nav_frame'], 'map')
        self.assertEqual(entry['meta']['map'], 'mixed_test_map')

    def test_splice_prefers_current_node_then_valid_selection(self) -> None:
        """Verify splicing prefers the current node, then a valid selection, with no backfill."""
        cases = [('CURRENT', 'SELECTED', 'CURRENT'), ('missing', 'SELECTED', 'SELECTED')]
        cases.extend((value, 'SELECTED', 'SELECTED') for value in ('—', 'none', 'None', '', None))
        cases.append(('missing', 'absent', None))
        for current, selected, expected in cases:
            with self.subTest(current=current, selected=selected):
                node = make_node(SWATHS)
                for name in ('CURRENT', 'SELECTED'):
                    node.doc.insert_node(TopoNode(name=name, x=0, y=0))
                node.topo_vm.current_node = current
                node.topo_vm.selected_node = selected

                node.save('crop', 1)

                expected_edges = {expected: NAV_ACTION} if expected else {}
                self.assertEqual(targets(node.doc.get_node('CROP_R1_OUT')), expected_edges)
                self.assertEqual(targets(node.doc.get_node('CROP_R1_IN')), {
                    'CROP_R1_OUT': node.row_action, **expected_edges,
                })
                for name in ('CURRENT', 'SELECTED'):
                    self.assertEqual(list(node.doc.get_node(name).edges), [])
                suffix = f'spliced @ {expected}' if expected else 'standalone'
                self.assertTrue(node.save_status.endswith(suffix))


class TestRepairPersistence(unittest.TestCase):
    def test_missing_map_or_rows_never_persists(self) -> None:
        """Verify repair reports missing maps or row metadata without scheduling persistence."""
        for doc, error in ((None, 'map not loaded'), (TopoDoc(name='field'), 'no row nodes found')):
            with self.subTest(error=error):
                node = make_node([])
                node.doc = doc

                node.repair()

                self.assertEqual(node.save_status, f'ERROR: {error}')
                node.topology.persist_and_reload.assert_not_called()

    def test_repair_updates_memory_and_replays_edges_on_separate_persisted_document(self) -> None:
        """Verify repair updates memory and replays idempotently while preserving saved actions."""
        node = make_node([])
        node.row_action = 'limbic_row_follow'
        for name, role, y in (('IN', 'entry', 0), ('W1', 'waypoint', 5), ('OUT', 'exit', 10)):
            node.doc.insert_node(TopoNode(
                name=name, x=0, y=y, meta={'row_id': 1, 'row_role': role}))
        disk_doc = copy.deepcopy(node.doc)
        disk_doc.get_node('IN').add_edge('W1', action='authored')
        node.queue_writes()

        node.repair(connect_to='missing')

        self.assertEqual(node.save_status, 'ERROR: base node \'missing\' not in map')
        node.topology.persist_and_reload.assert_not_called()
        self.assertEqual(node.queued, [])
        with self.assertLogs(SERVICE_LOGGER, level='WARNING') as logs:
            node.service.repair_connectivity(node.row_action, connect_to='missing')

        self.assertEqual(targets(node.doc.get_node('IN')), {'W1': 'limbic_row_follow'})
        self.assertEqual(targets(node.doc.get_node('W1')), {'OUT': 'limbic_row_follow'})
        self.assertEqual(node.save_status, 'repair: adding 2 edges…')
        self.assertEqual([r.getMessage() for r in logs.records],
                         ["repair: connect_to='missing' not in map, ignoring"])
        modify = node.captured_modify()
        node.topology.persist_and_reload.assert_called_once()
        self.assertEqual(
            node.save_status, 'repair: wired 2 edges — NO SPLICE, chain still isolated')

        modify(disk_doc)

        self.assertEqual(targets(disk_doc.get_node('IN')), {'W1': 'authored'})
        self.assertEqual(targets(disk_doc.get_node('W1')), {'OUT': 'limbic_row_follow'})
        before = copy.deepcopy(disk_doc.to_dict())
        modify(disk_doc)
        self.assertEqual(disk_doc.to_dict(), before)

    def test_lone_endpoint_warns_and_does_not_schedule_empty_repair(self) -> None:
        """Verify a lone row end warns about headlands without scheduling an empty repair."""
        node = make_node([])
        node.doc.insert_node(TopoNode(
            name='IN', x=0, y=0, meta={'row_id': 7, 'row_role': 'entry'}))

        with self.assertLogs(SERVICE_LOGGER, level='WARNING') as logs:
            node.repair()

        self.assertEqual(node.save_status, 'repair: already wired (1 rows)')
        self.assertEqual(len(logs.records), 1)
        self.assertIn('skipping headland edges', logs.records[0].getMessage())
        node.topology.persist_and_reload.assert_not_called()


def odometry(x: float, y: float) -> SimpleNamespace:
    """Supply just the pose fields consumed by the save boundary."""
    return SimpleNamespace(pose=SimpleNamespace(pose=SimpleNamespace(
        position=SimpleNamespace(x=x, y=y))))


if __name__ == '__main__':
    unittest.main()
