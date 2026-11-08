"""A published farm map goes into the topological map with its identity, and any later edit takes it away."""
# pylint: disable=missing-docstring
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from devkit_ui.application_services.plan_application_service import PlanApplicationService
from devkit_ui.application_services.topology_edit_service import TopologyEditService
from devkit_ui.missions.lf_rows import LfRowError, resolve_lf_row
from devkit_ui.models import MAP_EDITED, TopoDoc, TopoNode
from devkit_ui.topo_dock_builder import DockBuild
from test_farm_map_import import DOCKS, ROUTES, resign, with_docks
from test_plan_import import ROW0, ROW1, _feat, _plan
from test_topology_persistence import ServiceCase

FIXTURE = Path(__file__).resolve().parents[3] / 'tests' / 'farm-map.geojson'
GPS = SimpleNamespace(latitude=48.0, longitude=3.6, status=SimpleNamespace(status=0))


class FarmMapSaveCase(ServiceCase):
    """A real topology service and map store, with the plan service on top, in simulation."""

    def setUp(self) -> None:
        self.make(TopoDoc(name='farm', nodes=[TopoNode(name='HOME', x=0.0, y=0.0)]))
        self.edits = TopologyEditService(self.app)
        telemetry = SimpleNamespace(latest_gps=GPS, latest_odom=None)
        self.plans = PlanApplicationService(self.app, self.edits, telemetry, is_sim=True,
                                            background=lambda work: work())
        self.statuses: list[str] = []

    def farm_map_text(self) -> str:
        return FIXTURE.read_text(encoding='utf-8')

    def import_and_save(self, text: str, *, overwrite: bool = False, prefix: str = 'FARM') -> None:
        outcome = self.plans.import_plan(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.plans.save_rows(outcome.planned, prefix, 1, overwrite, row_action='row_follow',
                             current_node=None, selected_node=None, on_status=self.statuses.append)

    def saved(self) -> TopoDoc:
        return self.store.load('farm')


class ImportTest(FarmMapSaveCase):
    def test_a_farm_map_import_carries_its_identity(self) -> None:
        planned = self.plans.import_plan(self.farm_map_text()).planned
        self.assertEqual(planned.map_hash8, 'c50f60d8')
        self.assertEqual(planned.lf_rows,
                         ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 2, 0), (2, 0, 0), (2, 1, 0)))
        self.assertEqual(planned.break_after, frozenset({1, 3}))
        self.assertTrue(planned.contour_used)

    def test_a_changed_farm_map_is_refused(self) -> None:
        doc = json.loads(self.farm_map_text())
        doc['features'][2]['geometry']['coordinates'][0][0] += 0.001
        outcome = self.plans.import_plan(json.dumps(doc))
        self.assertFalse(outcome.ok)
        self.assertIn('content_hash does not match', outcome.error)

    def test_a_retired_farm_map_is_refused(self) -> None:
        doc = json.loads(self.farm_map_text())
        doc['properties']['status'] = 'retired'
        self.assertIn('only a published map', self.plans.import_plan(json.dumps(doc)).error)

    def test_a_plain_plan_has_no_identity(self) -> None:
        planned = self.plans.import_plan(_plan([_feat(ROW0, 0), _feat(ROW1, 1)])).planned
        self.assertIsNone(planned.map_hash8)


class SaveTest(FarmMapSaveCase):
    def test_save_stamps_hash8_and_writes_lf_metadata(self) -> None:
        self.import_and_save(self.farm_map_text())
        self.assertEqual(self.statuses[-1].split(' — ')[0].split(' · ')[0], 'saved 6 rows')
        for doc in (self.saved(), self.domain.get_doc()):
            self.assertEqual(doc.map_hash8, 'c50f60d8')
            self.assertEqual(doc.meta['hash8'], 'c50f60d8')

    def test_a_row_is_found_by_field_and_row_after_a_reload(self) -> None:
        self.import_and_save(self.farm_map_text())
        doc = self.saved()
        self.assertEqual(resolve_lf_row(doc, 1, 0), ['FARM_R1_IN'])
        self.assertEqual(resolve_lf_row(doc, 1, 1), ['FARM_R2_IN', 'FARM_R3_IN'])  # a row split in two
        self.assertEqual(resolve_lf_row(doc, 2, 1), ['FARM_R6_IN'])
        with self.assertRaises(LfRowError):
            resolve_lf_row(doc, 3, 0)

    def test_importing_again_without_overwrite_is_not_the_farm_map(self) -> None:
        """The first rows stay and the second set clashes, so the map is not the farm map."""
        self.import_and_save(self.farm_map_text())
        self.import_and_save(self.farm_map_text())
        self.assertEqual(self.saved().map_hash8, MAP_EDITED)
        self.assertIn('remote missions are refused', self.statuses[-1])

    def test_importing_again_with_overwrite_is_the_farm_map(self) -> None:
        self.import_and_save(self.farm_map_text())
        self.import_and_save(self.farm_map_text(), overwrite=True)
        self.assertEqual(self.saved().map_hash8, 'c50f60d8')
        self.assertNotIn('remote missions are refused', self.statuses[-1])

    def test_other_row_nodes_in_the_map_stop_it_being_the_farm_map(self) -> None:
        """Rows from an earlier plan under another prefix make a (field, row) ambiguous."""
        self.import_and_save(self.farm_map_text(), prefix='OLD')
        self.import_and_save(self.farm_map_text(), prefix='NEW')
        self.assertEqual(self.saved().map_hash8, MAP_EDITED)

    def test_a_plain_plan_save_leaves_no_identity(self) -> None:
        self.import_and_save(_plan([_feat(ROW0, 0), _feat(ROW1, 1)]))
        self.assertEqual(self.saved().map_hash8, MAP_EDITED)
        self.assertNotIn('hash8', self.saved().meta)


class DockSaveTest(FarmMapSaveCase):
    """The docks of a farm map become nodes joined to the row end their route names."""

    def docks_text(self, to_node: str = 'F1R0N0') -> str:
        doc = with_docks(json.loads(self.farm_map_text()))
        doc['features'][-1]['properties']['to_node'] = to_node
        return json.dumps(resign(doc, DOCKS, [replace(route, to_node=to_node) for route in ROUTES]))

    def edges(self, name: str) -> set[str]:
        return {e.node for e in self.saved().get_node(name).edges}

    def test_docks_are_saved_as_nodes(self) -> None:
        self.import_and_save(self.docks_text())
        dock = self.saved().get_node('FARM_D_DOCK')
        self.assertEqual((dock.meta['role'], dock.meta['farm_name'], dock.meta['heading_deg']), ('dock', 'dock', 1.0))
        self.assertIsNone(self.saved().get_node('FARM_D_DOCK1').meta['heading_deg'])
        self.assertEqual(self.saved().get_node('FARM_D_ROUTE1_1').meta['role'], 'route')
        self.assertIn('2 docks', self.statuses[-1])

    def test_the_route_joins_the_dock_to_the_row_end_both_ways(self) -> None:
        self.import_and_save(self.docks_text())
        self.assertEqual(self.edges('FARM_D_DOCK'), {'FARM_D_ROUTE1_1'})
        self.assertEqual(self.edges('FARM_D_ROUTE1_1'), {'FARM_D_DOCK', 'FARM_R1_IN'})
        self.assertIn('FARM_D_ROUTE1_1', self.edges('FARM_R1_IN'))

    def test_a_later_index_ends_the_route_at_the_row_exit(self) -> None:
        self.import_and_save(self.docks_text('F1R0N1'))
        self.assertIn('FARM_D_ROUTE1_1', self.edges('FARM_R1_OUT'))
        self.assertNotIn('FARM_D_ROUTE1_1', self.edges('FARM_R1_IN'))

    def test_a_complete_save_with_docks_is_the_farm_map(self) -> None:
        """The stamp is the hash8 of the map with its docks, not of its rows."""
        text = self.docks_text()
        self.import_and_save(text)
        self.assertEqual(self.saved().map_hash8, json.loads(text)['properties']['content_hash'][:8])
        self.assertNotEqual(self.saved().map_hash8, 'c50f60d8')
        self.assertNotIn('remote missions are refused', self.statuses[-1])

    def test_importing_again_with_overwrite_does_not_duplicate_docks(self) -> None:
        self.import_and_save(self.docks_text())
        self.import_and_save(self.docks_text(), overwrite=True)
        names = sorted(n.name for n in self.saved().nodes if n.meta.get('dock_prefix'))
        self.assertEqual(names, ['FARM_D_DOCK', 'FARM_D_DOCK1', 'FARM_D_ROUTE1_1'])
        self.assertEqual(self.edges('FARM_D_ROUTE1_1'), {'FARM_D_DOCK', 'FARM_R1_IN'})
        self.assertEqual(self.saved().map_hash8, json.loads(self.docks_text())['properties']['content_hash'][:8])

    def test_a_route_to_a_row_that_is_not_there_leaves_the_dock_unlinked(self) -> None:
        self.import_and_save(self.docks_text('F9R9N0'))
        self.assertEqual(self.edges('FARM_D_DOCK'), set())
        self.assertFalse(self.saved().has_node('FARM_D_ROUTE1_1'))
        self.assertEqual(self.saved().map_hash8, MAP_EDITED)
        self.assertIn('remote missions are refused', self.statuses[-1])

    def test_a_dock_from_another_save_stops_it_being_the_farm_map(self) -> None:
        stray = TopoNode(name='OTHER_D_DOCK', x=0.0, y=0.0, meta={'role': 'dock', 'dock_prefix': 'OTHER'})
        doc = TopoDoc(name='farm', nodes=[stray])
        built = DockBuild(nodes={'FARM_D_DOCK': TopoNode(name='FARM_D_DOCK', x=1.0, y=1.0)})
        holds = TopologyEditService._holds_only_these_docks  # pylint: disable=protected-access
        self.assertFalse(holds(doc, built, 'FARM', overwrite=True))
        self.assertFalse(holds(doc, None, 'FARM', overwrite=True))
        self.assertTrue(holds(TopoDoc(name='farm', nodes=[]), built, 'FARM', overwrite=False))
        self.assertTrue(holds(TopoDoc(name='farm', nodes=[]), None, 'FARM', overwrite=False))

    def test_the_docks_of_an_earlier_save_with_this_prefix_are_replaced(self) -> None:
        old = TopoNode(name='FARM_D_DOCK', x=0.0, y=0.0, meta={'role': 'dock', 'dock_prefix': 'FARM'})
        holds = TopologyEditService._holds_only_these_docks  # pylint: disable=protected-access
        self.assertTrue(holds(TopoDoc(name='farm', nodes=[old]), None, 'FARM', overwrite=True))
        self.assertFalse(holds(TopoDoc(name='farm', nodes=[old]), None, 'FARM', overwrite=False))

    def test_a_farm_map_without_docks_adds_none(self) -> None:
        self.import_and_save(self.farm_map_text())
        self.assertFalse([n for n in self.saved().nodes if n.meta.get('dock_prefix')])


class EditTest(FarmMapSaveCase):
    """Whatever changes the map afterwards, it is no longer the farm map."""

    def setUp(self) -> None:
        super().setUp()
        self.import_and_save(self.farm_map_text())
        self.assertEqual(self.saved().map_hash8, 'c50f60d8')

    def test_deleting_a_node(self) -> None:
        self.app.persist_and_reload(self.edits.stage_delete_node('HOME').modify)
        self.assertEqual(self.saved().map_hash8, MAP_EDITED)
        self.assertEqual(self.domain.get_doc().map_hash8, MAP_EDITED)

    def test_deleting_a_row(self) -> None:
        self.app.persist_and_reload(self.edits.stage_delete_row(3).modify)
        self.assertEqual(self.saved().map_hash8, MAP_EDITED)

    def test_dropping_a_node_marks_the_live_map_at_once_and_the_file_when_saved(self) -> None:
        drop = self.edits.stage_drop('GATE', None, 'entry', pose=(1.0, 2.0), is_sim=True, gps=GPS,
                                     row_action='row_follow', current_node='HOME', selected_node=None)
        self.assertEqual(self.domain.get_doc().map_hash8, MAP_EDITED)
        self.assertEqual(self.saved().map_hash8, 'c50f60d8')  # not written yet
        self.app.drop_node(drop.node)
        self.assertEqual(self.saved().map_hash8, MAP_EDITED)

    def test_repair_marks_the_live_map(self) -> None:
        for node in self.domain.get_doc().nodes:
            node.remove_edges({e.node for e in node.edges if e.action == 'row_follow'})
        self.edits.stage_repair('row_follow')
        self.assertEqual(self.domain.get_doc().map_hash8, MAP_EDITED)

    def test_patching_a_role(self) -> None:
        self.app.patch_node_role('FARM_R1_IN', 'exit')
        saved = self.saved()
        self.assertEqual(saved.map_hash8, MAP_EDITED)
        node = saved.get_node('FARM_R1_IN')
        self.assertEqual((node.meta['row_role'], node.properties.row_role), ('exit', 'exit'))

    def test_changing_the_row_action(self) -> None:
        self.app.persist_and_reload(lambda doc: doc.set_row_action({'row_follow'}, 'vision_row_follow'))
        self.assertEqual(self.saved().map_hash8, MAP_EDITED)

    def test_a_copy_of_the_map_keeps_the_identity(self) -> None:
        """Robots get the map by file copy: the meta must survive a rename and a clone."""
        doc = self.saved()
        self.assertEqual(doc.renamed('robot_two').map_hash8, 'c50f60d8')
        self.assertEqual(doc.clone_empty('robot_two').map_hash8, 'c50f60d8')


if __name__ == '__main__':
    unittest.main()
