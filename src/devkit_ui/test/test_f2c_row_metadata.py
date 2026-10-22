# PlanHarness supplies the services and fixes normally provided by the ROS node.
# pylint: disable=attribute-defined-outside-init,no-member,protected-access
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from devkit_ui import row_planning, topo_row_builder
from devkit_ui.application_services import plan_application_service as plan_module
from devkit_ui.application_services.plan_application_service import PlanApplicationService
from devkit_ui.application_services.topology_edit_service import TopologyEditService
from devkit_ui.constants import NAV_ACTION
from devkit_ui.models import TopoDoc, TopoEdge, TopoNode
from devkit_ui.plan_types import PlannedRows, PlanRequest
from devkit_ui.topo_results import PersistResult
from devkit_ui.view_models.map_context import MapContext
from devkit_ui.view_models.plan_view_model import PlanPageState, PlanViewModel

ROW_ACTION = 'row_follow'
SERVICE_LOGGER = plan_module.__name__


def latlon_to_xy(
    lat: float, lon: float, anchor_lat: float, anchor_lon: float,
) -> tuple[float, float]:
    return lat - anchor_lat, lon - anchor_lon


def xy_to_latlon(x: float, y: float, anchor_lat: float, anchor_lon: float) -> tuple[float, float]:
    return anchor_lat + x, anchor_lon + y


def resample_row_xy(_points, _anchor_lat, _anchor_lon, _interval):
    return [(1.0, 1.5)]


def patch_row_geometry(test: unittest.TestCase) -> None:
    """Replace projection and resampling with simple stand-ins so expected poses stay readable."""
    test.enterContext(patch.multiple(
        topo_row_builder,
        f2c_latlon_to_xy=latlon_to_xy,
        f2c_xy_to_latlon=xy_to_latlon,
        resample_row_xy=resample_row_xy,
    ))


class PlanHarness:
    """The real plan service and view model over an in-memory map, GPS, odometry and selection.

    Writes run inline and are applied to ``doc`` unless queue_writes() defers them.
    The planned rows are set through swaths, origin_ll, contour_used and break_after.
    """

    def __init__(self, swaths, contour_used=False) -> None:
        self.doc: TopoDoc | None = TopoDoc(name='field')
        self.run_vm = SimpleNamespace(drop_node=SimpleNamespace(row_action=ROW_ACTION))
        self.latest_odom = None
        self.latest_gps = SimpleNamespace(
            latitude=51.0,
            longitude=-2.0,
            status=SimpleNamespace(status=0),
        )
        self.topo_vm = SimpleNamespace(current_node=None, selected_node=None)
        self.page = PlanPageState()
        self.queued: list = []
        self._queue_writes = False

        self.topology = SimpleNamespace(
            get_doc=lambda: self.doc,
            persist_and_reload=Mock(side_effect=self._apply),
        )
        self.service = PlanApplicationService(
            self.topology, TopologyEditService(self.topology), self, is_sim=True,
            background=self._background)
        self.vm = PlanViewModel(self.service, MapContext(self, self.run_vm))
        self._planned = {
            'swaths': swaths, 'origin_ll': (51.0, -2.0), 'contour_used': contour_used,
            'break_after': frozenset(),
        }
        self._sync()

    @property
    def row_action(self) -> str:
        """Return the row action as the Run tab holds it."""
        return self.run_vm.drop_node.row_action

    @row_action.setter
    def row_action(self, action: str) -> None:
        """Select a row action the way the Run tab toggle does."""
        self.run_vm.drop_node.row_action = action

    @property
    def topo_doc(self):
        """The loaded map, as the topology view model would mirror it."""
        return self.doc

    @property
    def current_node(self):
        return self.topo_vm.current_node

    @property
    def selected_node(self):
        return self.topo_vm.selected_node

    @property
    def is_sim(self) -> bool:
        return self.service._is_sim

    @is_sim.setter
    def is_sim(self, value: bool) -> None:
        self.service._is_sim = value

    @property
    def save_status(self) -> str:
        return self.page.save_status

    def _apply(self, modify):
        modify(self.doc)
        return PersistResult('skipped')

    def _background(self, work) -> None:
        if self._queue_writes:
            self.queued.append(work)
        else:
            work()

    def queue_writes(self) -> None:
        """Hold writes back so the status shown while writing can be checked."""
        self._queue_writes = True

    def captured_modify(self):
        """Run the queued write without applying it to doc and return its map edit."""
        self.topology.persist_and_reload.side_effect = lambda modify: PersistResult('skipped')
        self.queued.pop()()
        return self.topology.persist_and_reload.call_args.args[0]

    def _sync(self) -> None:
        self.draft = PlannedRows(**self._planned)

    def plan(self, request):
        """Plan as the page does: keep the rows of a successful run as the draft."""
        view = asyncio.run(self.vm.plan(request))
        if view.ok:
            self._keep(view.planned)
        return view

    def import_plan(self, text):
        """Import as the page does: keep the rows of a successful import as the draft."""
        view = self.vm.import_plan(text)
        if view.ok:
            self._keep(view.planned)
        return view

    def _keep(self, planned) -> None:
        self._planned = {'swaths': planned.swaths, 'origin_ll': planned.origin_ll,
                         'contour_used': planned.contour_used, 'break_after': planned.break_after}
        self._sync()

    def save(self, prefix, row_id_start, overwrite=False) -> None:
        self.vm.save_rows(self.draft, prefix, row_id_start, overwrite, self.page.set_save_status)

    def repair(self, connect_to=None) -> None:
        self.vm.repair(connect_to, self.page.set_save_status)


def _planned_property(name):
    def getter(self):
        return self._planned[name]

    def setter(self, value):
        self._planned[name] = value
        self._sync()
    return property(getter, setter)


for _name in ('swaths', 'origin_ll', 'contour_used', 'break_after'):
    setattr(PlanHarness, _name, _planned_property(_name))


def make_node(swaths, *, contour_used=False):
    """Build a plan harness holding the supplied swaths, with in-memory persistence."""
    return PlanHarness(swaths, contour_used=contour_used)


class TestF2CRowMetadata(unittest.TestCase):
    def setUp(self) -> None:
        patch_row_geometry(self)

    def test_repair_preserves_existing_modes_and_is_idempotent_after_mode_change(self) -> None:
        """Verify repair preserves existing actions and skips saving fully connected rows."""
        node = make_node([])
        node.row_action = 'limbic_row_follow'
        for name, role, y in (('IN', 'entry', 0), ('WP', 'waypoint', 5), ('OUT', 'exit', 10)):
            node.doc.insert_node(TopoNode(
                name=name, x=0, y=y, meta={'row_id': 1, 'row_role': role}))
        existing = TopoEdge('row_traversal', 'authored', 'WP')
        node.doc.get_node('IN').add_edge(existing)

        node.repair()

        self.assertEqual(list(node.doc.get_node('IN').edges), [existing])
        self.assertEqual([edge.action for edge in node.doc.get_node('WP').edges],
                         ['limbic_row_follow'])
        node.topology.persist_and_reload.assert_called_once()
        node.topology.persist_and_reload.reset_mock()
        node.row_action = 'row_traversal'

        node.repair()

        self.assertEqual(list(node.doc.get_node('IN').edges), [existing])
        self.assertEqual([edge.action for edge in node.doc.get_node('WP').edges],
                         ['limbic_row_follow'])
        self.assertIn('already wired', node.save_status)
        node.topology.persist_and_reload.assert_not_called()

    def test_selected_mode_applies_to_straight_and_contour_chains_only(self) -> None:
        """Verify both swath types use the selected row mode and navigation for headlands."""
        for action in ('row_traversal', 'limbic_row_follow'):
            for contour in (False, True):
                with self.subTest(action=action, contour=contour):
                    node = make_node([
                        [(51.0, -2.0), (51.0005, -2.0), (51.001, -2.0)],
                        [(51.001, -1.999), (51.0005, -1.999), (51.0, -1.999)],
                    ], contour_used=contour)
                    node.row_action = action
                    node.save('crop', row_id_start=1)
                    row_edges = []
                    headland_edges = []
                    for source in node.doc.nodes:
                        for edge in source.edges:
                            target = node.doc.get_node(edge.node)
                            if source.meta['row_id'] == target.meta['row_id']:
                                row_edges.append(edge)
                            else:
                                headland_edges.append(edge)
                    self.assertEqual(len(row_edges), 4 if contour else 2)
                    self.assertEqual({edge.action for edge in row_edges}, {action})
                    self.assertTrue(headland_edges)
                    self.assertEqual({edge.action for edge in headland_edges}, {NAV_ACTION})

    def test_repair_uses_selected_mode_for_each_waypoint_and_navigation_for_headlands(self):
        """Verify repair applies row actions along waypoint chains and navigation between rows."""
        for action in ('row_traversal', 'limbic_row_follow'):
            with self.subTest(action=action):
                node = make_node([])
                node.row_action = action
                for name, row_id, role, x, y in (
                    ('R1_IN', 1, 'entry', 0, 0), ('R1_W1', 1, 'waypoint', 0, 5),
                    ('R1_OUT', 1, 'exit', 0, 10), ('R2_IN', 2, 'entry', 2, 0),
                    ('R2_OUT', 2, 'exit', 2, 10),
                ):
                    node.doc.insert_node(TopoNode(
                        name=name, x=x, y=y, meta={'row_id': row_id, 'row_role': role}))

                node.repair()

                actual = {(source.name, edge.node): edge.action
                          for source in node.doc.nodes for edge in source.edges}
                self.assertEqual(actual, {
                    ('R1_IN', 'R1_W1'): action, ('R1_W1', 'R1_OUT'): action,
                    ('R2_IN', 'R2_OUT'): action,
                    ('R1_IN', 'R2_IN'): NAV_ACTION, ('R2_IN', 'R1_IN'): NAV_ACTION,
                    ('R1_OUT', 'R2_OUT'): NAV_ACTION, ('R2_OUT', 'R1_OUT'): NAV_ACTION,
                })
                node.topology.persist_and_reload.assert_called_once()

    def assert_row_metadata(self, node: TopoNode, row_id: int, row_role: str) -> None:
        self.assertEqual(node.meta['row_id'], row_id)
        self.assertEqual(node.meta['row_role'], row_role)
        properties = node.to_dict()['node']['properties']
        self.assertEqual(properties['row_id'], row_id)
        self.assertEqual(properties['row_role'], row_role)

    def test_entry_and_exit_metadata_is_saved_for_each_row(self) -> None:
        node = make_node([
            [(51.0, -2.0), (51.001, -1.999)],
            [(51.002, -2.0), (51.003, -1.999)],
        ])

        node.save('crop', row_id_start=7)

        expected = {
            'CROP_R7_IN': (7, 'entry'),
            'CROP_R7_OUT': (7, 'exit'),
            'CROP_R8_IN': (8, 'entry'),
            'CROP_R8_OUT': (8, 'exit'),
        }
        self.assertEqual({saved.name for saved in node.doc.nodes}, set(expected))
        for name, (row_id, row_role) in expected.items():
            with self.subTest(name=name):
                self.assert_row_metadata(node.doc.get_node(name), row_id, row_role)

    def test_contour_waypoint_metadata_matches_entry_and_exit_metadata(self) -> None:
        node = make_node(
            [[(51.0, -2.0), (51.0005, -1.9995), (51.001, -1.999)]],
            contour_used=True,
        )

        node.save('curve', row_id_start=3)

        expected = {
            'CURVE_R3_IN': 'entry',
            'CURVE_R3_W1': 'waypoint',
            'CURVE_R3_OUT': 'exit',
        }
        self.assertEqual({saved.name for saved in node.doc.nodes}, set(expected))
        for name, row_role in expected.items():
            with self.subTest(name=name):
                self.assert_row_metadata(node.doc.get_node(name), 3, row_role)


class TestDirectContourPlanning(unittest.TestCase):
    def setUp(self) -> None:
        patch_row_geometry(self)

    def test_fragment_breaks_reach_topo_and_reset_for_straight_plans(self):
        swaths = [
            [(51.0, -2.0), (51.001, -2.0)],
            [(51.002, -2.0), (51.003, -2.0)],
            [(51.003, -1.999), (51.0, -1.999)],
        ]
        node = make_node([])

        def plan_contours(*_args, break_after):
            break_after.add(0)
            return swaths

        select_contour = Mock(return_value=swaths[0])
        self.enterContext(patch.multiple(
            row_planning,
            run_contour_f2c=plan_contours,
            load_recon_points=Mock(return_value=([], [], [])),
            build_elevation_grid=Mock(return_value=([], (0, 0), 0)),
            field_centroid_xy=Mock(return_value=(0, 0)),
            select_reference_contour_latlon=select_contour,
            np=Mock(),
        ))
        self.enterContext(patch.object(plan_module, 'run_f2c', Mock(return_value=swaths)))
        request = PlanRequest(
            corners_ll=[(51.0, -2.0), (51.003, -2.0), (51.0, -1.999)], tool_width=4,
            snake=True, contour=True, recon_path='test.csv')

        outcome = node.plan(request)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.swaths, swaths)
        self.assertEqual(node.draft.break_after, {0})
        self.assertTrue(node.draft.contour_used)
        node.save('curve', row_id_start=7)
        nodes = node.doc
        self.assertNotIn('CURVE_R8_IN', [e.node for e in nodes.get_node('CURVE_R7_OUT').edges])
        self.assertNotIn('CURVE_R7_OUT', [e.node for e in nodes.get_node('CURVE_R8_IN').edges])
        self.assertIn('CURVE_R9_IN', [e.node for e in nodes.get_node('CURVE_R8_OUT').edges])

        select_contour.return_value = None
        self.assertTrue(node.plan(request).ok)
        self.assertEqual(node.draft.break_after, set())
        self.assertFalse(node.draft.contour_used)

        node.break_after = {0}
        node.contour_used = True
        self.assertTrue(node.plan(PlanRequest(
            corners_ll=request.corners_ll, tool_width=4, contour=False)).ok)
        self.assertEqual(node.draft.break_after, set())
        self.assertFalse(node.draft.contour_used)


if __name__ == '__main__':
    unittest.main()
