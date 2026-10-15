"""Failure and deferred-write regression tests for the UI row-builder adapter."""

# NOTE: The existing harness supplies the state normally initialized by ROS.
# pylint: disable=no-member,protected-access
import copy
import unittest

from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.test_f2c_row_metadata import SERVICE_LOGGER, make_node, patch_row_geometry
from devkit_ui.test_f2c_row_persistence import SWATHS
from devkit_ui.test_topo_row_builder import targets


class TestSaveRegressions(unittest.TestCase):
    def setUp(self) -> None:
        """Keep geometry deterministic while exercising the real save and builder code."""
        patch_row_geometry(self)

    def test_exact_gps_zero_threshold_is_accepted(self) -> None:
        """The rejection region excludes the boundary at one nanodegree."""
        for lat, lon in ((1e-9, 0.0), (-1e-9, 0.0), (0.0, 1e-9), (0.0, -1e-9)):
            with self.subTest(lat=lat, lon=lon):
                node = make_node(SWATHS)
                node.latest_gps.latitude = lat
                node.latest_gps.longitude = lon

                node.save('crop', 1)

                node.topology.persist_and_reload.assert_called_once()
                self.assertEqual(node.save_status, 'saved 1 rows · CROP · standalone')

    def test_later_swath_error_does_not_persist_partial_build_or_delete_old_rows(self) -> None:
        """A synchronous builder failure leaves existing rows intact even with overwrite."""
        node = make_node([*SWATHS, [(51.0, -2.0), (51.5,)]])
        node.doc.insert_node(TopoNode(name='CROP_R99_IN', x=10, y=20))
        before = copy.deepcopy(node.doc.to_dict())

        with self.assertRaises(ValueError):
            node.save('crop', 1, overwrite=True)

        node.topology.persist_and_reload.assert_not_called()
        self.assertEqual(node.doc.to_dict(), before)

    def test_unusable_batch_does_not_clear_existing_rows_with_overwrite(self) -> None:
        """The save gate prevents applying an empty replacement to an existing map."""
        node = make_node([[], [(51.0, -2.0)]])
        node.doc.insert_node(TopoNode(name='CROP_R99_IN', x=10, y=20))
        before = copy.deepcopy(node.doc.to_dict())

        node.save('crop', 1, overwrite=True)

        self.assertIn('nothing added', node.save_status)
        node.topology.persist_and_reload.assert_not_called()
        self.assertEqual(node.doc.to_dict(), before)

    def test_deferred_save_preserves_disk_collision_and_captured_plan(self) -> None:
        """Use the planned nodes while preserving a node added to disk before the callback."""
        node = make_node(SWATHS)
        node.queue_writes()
        node.save('crop', 7)
        modify = node.captured_modify()
        node.topology.persist_and_reload.assert_called_once()
        node.swaths = []
        node.row_action = 'changed_after_save'
        existing = TopoNode(name='CROP_R7_OUT', x=99, y=88, meta={'note': 'surveyed'})
        existing.add_edge('DOCK', action='authored')
        before = copy.deepcopy(existing.to_dict())
        disk_doc = TopoDoc(name='field', nodes=[existing, TopoNode(name='DOCK', x=0, y=0)])

        modify(disk_doc)

        self.assertEqual({entry.name for entry in disk_doc.nodes}, {
            'CROP_R7_IN', 'CROP_R7_OUT', 'DOCK',
        })
        self.assertIs(disk_doc.get_node(existing.name), existing)
        self.assertEqual(existing.to_dict(), before)
        self.assertEqual(targets(disk_doc.get_node('CROP_R7_IN')), {'CROP_R7_OUT': 'row_follow'})
        self.assertEqual(list(node.doc.nodes), [])


class TestRepairRegressions(unittest.TestCase):
    def test_infinite_row_id_does_not_partially_repair_or_persist(self) -> None:
        """Plan failure occurs before missing edges are added to the live map."""
        node = make_node([])
        for name, role, row_id, y in (
            ('IN', 'entry', 1, 0), ('OUT', 'exit', 1, 10),
            ('BAD', 'entry', float('inf'), 20),
        ):
            node.doc.insert_node(TopoNode(name=name, x=0, y=y, meta={
                'row_id': row_id, 'row_role': role,
            }))
        before = copy.deepcopy(node.doc.to_dict())

        with self.assertRaises(OverflowError):
            node.repair()

        node.topology.persist_and_reload.assert_not_called()
        self.assertEqual(node.doc.to_dict(), before)

    def test_valid_connect_to_is_status_only_and_never_splices_repaired_rows(self) -> None:
        """A repair target annotates status without adding edges to or from the target."""
        node = make_node([])
        for name, role, y in (('IN', 'entry', 0), ('OUT', 'exit', 10)):
            node.doc.insert_node(TopoNode(name=name, x=0, y=y, meta={
                'row_id': 1, 'row_role': role,
            }))
        dock = TopoNode(name='DOCK', x=1, y=1)
        dock.add_edge('OUT', action=NAV_ACTION)
        node.doc.insert_node(dock)
        before = copy.deepcopy(dock.to_dict())

        with self.assertNoLogs(SERVICE_LOGGER, level='WARNING'):
            node.repair(connect_to='DOCK')

        node.topology.persist_and_reload.assert_called_once()
        self.assertEqual(node.save_status, 'repair: wired 1 edges @ DOCK')
        self.assertEqual(targets(node.doc.get_node('IN')), {'OUT': node.row_action})
        self.assertEqual(targets(node.doc.get_node('OUT')), {})
        self.assertEqual(dock.to_dict(), before)


if __name__ == '__main__':
    unittest.main()
