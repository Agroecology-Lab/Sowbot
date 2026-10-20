# pylint: disable=protected-access
import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from devkit_ui.constants import ROW_ACTION, VISION_ROW_ACTION
from devkit_ui.models import TopoDoc, TopoNode
from devkit_ui.parse import dump_topo_yaml, parse_topo_yaml
from devkit_ui.topo_defaults import default_actions, default_definitions
from topo_test_fakes import attach_authoring, patch_store_io, run_threads_inline


def load_map_harness(directory, case):
    """Build the Run tab's view models over real map storage in a temporary directory."""
    boundaries = patch_store_io(case)
    run_threads_inline(case)
    node = SimpleNamespace(
        _topo_doc=TopoDoc(name='live', nodes=[TopoNode(name='A', x=1.0, y=2.0)]))
    attach_authoring(node, directory)
    node._topo_vm.update_doc(node._topo_doc)
    return node, boundaries


def persist_and_reload(node, modify, success='updated'):
    """Persist modify to the saved map, reporting the outcome on the drop-node status."""
    node._topo_app_service.persist_and_reload_async(
        modify, success, lambda status: setattr(node._run_vm.drop_node, 'status', status))


class TestMapOperations(unittest.TestCase):
    def setUp(self) -> None:
        """Create an isolated map harness with a temporary persistence directory."""
        # pylint: disable-next=consider-using-with
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.node, self.namespace = load_map_harness(self.directory, self)

    def test_save_prefers_persisted_state_and_leaves_live_map_untouched(self) -> None:
        """Verify a named copy uses disk state and leaves the live map unchanged."""
        persisted = TopoDoc(name='live', metric_map='occupancy',
                            nodes=[TopoNode(name='DISK', x=9.0, y=8.0)])
        dump_topo_yaml(persisted, self.directory / 'live')
        original_bytes = (self.directory / 'live').read_bytes()
        live = self.node._topo_doc
        original = copy.deepcopy(live.to_dict())

        self.assertEqual(self.node._topo_vm.save_map_as('  north_field-2026  '),
                         'saved → north_field-2026')

        saved = parse_topo_yaml(self.directory / 'north_field-2026')
        self.assertEqual(saved.name, 'north_field-2026')
        self.assertEqual({node.name for node in saved.nodes}, {'DISK'})
        self.assertEqual(saved.get_node('DISK').meta['map'], 'north_field-2026')
        self.assertEqual(saved.to_dict()['metric_map'], 'occupancy')
        self.assertEqual((self.directory / 'live').read_bytes(), original_bytes)
        self.assertIs(self.node._topo_doc, live)
        self.assertEqual(live.to_dict(), original)
        self.node._topo_map_pub.publish.assert_not_called()

    def test_save_without_disk_source_accepts_name_boundaries(self) -> None:
        """Verify one-character and 64-character names work when copying an in-memory map."""
        for name in ('7', 'a' * 64):
            with self.subTest(name=name):
                self.assertEqual(self.node._topo_vm.save_map_as(name), f'saved → {name}')
                saved = parse_topo_yaml(self.directory / name)
                self.assertTrue(saved.has_node('A'))
                self.assertEqual(saved.name, name)
        self.assertFalse((self.directory / 'live').exists())
        self.assertEqual(self.node._topo_doc.name, 'live')

    def test_invalid_names_never_touch_filesystem(self) -> None:
        """Verify invalid copy names are rejected before any filesystem operations."""
        for name in (None, '', ' ', '../escape', '/absolute', 'a/b', 'a\\b', '.hidden',
                     '_field', '-field', 'two words', 'field.yaml', 'é', 'a' * 65, 'a\nb'):
            with self.subTest(name=name):
                self.assertTrue(self.node._topo_vm.save_map_as(name).startswith('ERROR: use letters'))
        self.namespace['parse_topo_yaml'].assert_not_called()
        self.namespace['dump_topo_yaml'].assert_not_called()
        self.namespace['os'].makedirs.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_save_refuses_live_name_and_existing_target(self) -> None:
        """Verify saving refuses the live map name and preserves an existing target file."""
        target = self.directory / 'existing'
        target.write_text('keep me', encoding='utf-8')
        self.assertEqual(self.node._topo_vm.save_map_as(' live '), 'ERROR: that is the live map name')
        self.assertEqual(self.node._topo_vm.save_map_as('existing'), 'ERROR: existing already exists')
        self.assertEqual(target.read_text(encoding='utf-8'), 'keep me')
        self.namespace['dump_topo_yaml'].assert_not_called()

    def test_save_rejects_unloaded_and_empty_maps(self) -> None:
        """Verify saving rejects missing maps and empty maps in memory or on disk."""
        self.node._topo_doc = None
        self.assertEqual(self.node._topo_vm.save_map_as('copy'), 'ERROR: map not loaded')
        self.node._topo_doc = TopoDoc(name='live')
        self.assertEqual(self.node._topo_vm.save_map_as('copy'), 'ERROR: map has no nodes')
        self.node._topo_doc.add_node(TopoNode(name='MEMORY'))
        dump_topo_yaml(TopoDoc(name='live'), self.directory / 'live')
        self.assertEqual(self.node._topo_vm.save_map_as('copy'), 'ERROR: map has no nodes')
        self.namespace['dump_topo_yaml'].assert_not_called()

    def test_save_reports_read_and_write_errors_without_changing_live_map(self) -> None:
        """Verify copy failures report disk errors and leave the live map unchanged."""
        dump_topo_yaml(self.node._topo_doc, self.directory / 'live')
        live = self.node._topo_doc
        for boundary in ('parse_topo_yaml', 'dump_topo_yaml'):
            with self.subTest(boundary=boundary):
                operation = self.namespace[boundary]
                original_effect = operation.side_effect
                operation.side_effect = OSError('disk unavailable')
                try:
                    self.assertEqual(self.node._topo_vm.save_map_as('copy'), 'ERROR: disk unavailable')
                    self.assertIs(self.node._topo_doc, live)
                    self.assertFalse((self.directory / 'copy').exists())
                finally:
                    operation.side_effect = original_effect
        self.node._topo_map_pub.publish.assert_not_called()

    def test_clear_archives_custom_settings_then_resets_defaults_and_row_mode(self) -> None:
        """Verify clearing archives custom settings and resets actions and row mode."""
        live = self.node._topo_doc
        live.seed_actions({'legacy': {'composable': True}}, {'legacy_bt': '<legacy/>'})
        live.transformation['translation'] = {'x': 42.0}
        original = copy.deepcopy(live.to_dict())
        self.node._run_vm.drop_node.row_action = VISION_ROW_ACTION

        self.assertEqual(self.node._topo_vm.archive_and_clear_map(), 'archived → live_1')

        archived = parse_topo_yaml(self.directory / 'live_1')
        self.assertTrue(archived.has_node('A'))
        self.assertEqual(archived.actions, original['actions'])
        self.assertEqual(archived.definitions, original['definitions'])
        cleared = parse_topo_yaml(self.directory / 'live')
        self.assertEqual(list(cleared.nodes), [])
        self.assertEqual(cleared.actions, default_actions())
        self.assertEqual(cleared.definitions, default_definitions())
        self.assertEqual(cleared.transformation, original['transformation'])
        self.assertEqual(self.node.authoring.row_action, ROW_ACTION)
        self.assertEqual(self.node._run_vm.drop_node.row_action, ROW_ACTION)
        self.assertEqual(live.to_dict(), original)
        self.node._topo_map_pub.publish.assert_called_once_with(self.node._topo_doc.to_dict())

    def test_clear_archives_disk_state_without_overwriting_previous_archive(self) -> None:
        """Verify clearing archives the saved map under the next available archive name."""
        dump_topo_yaml(TopoDoc(name='live', nodes=[TopoNode(name='DISK', x=0.0, y=0.0)]),
                       self.directory / 'live')
        previous = self.directory / 'live_1'
        previous.write_text('previous archive', encoding='utf-8')

        self.assertEqual(self.node._topo_vm.archive_and_clear_map(), 'archived → live_2')

        self.assertTrue(parse_topo_yaml(self.directory / 'live_2').has_node('DISK'))
        self.assertEqual(previous.read_text(encoding='utf-8'), 'previous archive')

    def test_failed_clear_keeps_live_document_and_row_mode(self) -> None:
        """Verify a failed archive write preserves the live document and row selection."""
        live = self.node._topo_doc
        self.node._run_vm.drop_node.row_action = VISION_ROW_ACTION
        self.namespace['dump_topo_yaml'].side_effect = OSError('read only')

        self.assertEqual(self.node._topo_vm.archive_and_clear_map(), 'ERROR: read only')

        self.assertIs(self.node._topo_doc, live)
        self.assertEqual(self.node.authoring.row_action, VISION_ROW_ACTION)
        self.assertEqual(self.node._run_vm.drop_node.row_action, VISION_ROW_ACTION)
        self.node._topo_map_pub.publish.assert_not_called()

    def test_deleting_a_node_clears_its_selection_and_persists(self) -> None:
        """Verify delete removes the node from the saved map and deselects it."""
        live = self.node._topo_doc
        live.add_node(TopoNode(name='B', x=3.0, y=2.0))
        dump_topo_yaml(live, self.directory / 'live')
        self.node._topo_vm.set_selected_node('B')

        self.node.authoring.delete_node('B')

        self.assertIsNone(self.node._topo_vm.selected_node)
        self.assertEqual({n.name for n in parse_topo_yaml(self.directory / 'live').nodes}, {'A'})
        self.assertEqual(self.node._run_vm.topo.delete_status, 'deleted B — live (no srv)')

    def test_deleting_a_row_removes_all_its_nodes(self) -> None:
        """Verify deleting a row removes every node carrying its row id."""
        live = self.node._topo_doc
        for name, role in (('R_IN', 'entry'), ('R_OUT', 'exit')):
            live.add_node(TopoNode(name=name, x=4.0, y=4.0, meta={'row_id': 5, 'row_role': role}))
        dump_topo_yaml(live, self.directory / 'live')

        self.node.authoring.delete_row(5)

        self.assertEqual({n.name for n in parse_topo_yaml(self.directory / 'live').nodes}, {'A'})
        self.assertEqual(self.node._run_vm.topo.delete_status,
                         'deleted row 5 (2 nodes) — live (no srv)')

    def test_deleting_unknown_targets_reports_the_error(self) -> None:
        """Verify a missing node or row is reported and nothing is written."""
        self.node.authoring.delete_node('NOPE')
        self.assertEqual(self.node._run_vm.topo.delete_status, "ERROR: 'NOPE' not in map")
        self.node.authoring.delete_row(99)
        self.assertEqual(self.node._run_vm.topo.delete_status, 'ERROR: no nodes for row 99')
        self.namespace['dump_topo_yaml'].assert_not_called()

    def test_persist_seeds_missing_actions_and_preserves_custom_actions(self) -> None:
        """Verify new map files receive defaults only when the live map has no actions."""
        for custom in (False, True):
            with self.subTest(custom=custom):
                node, _ = load_map_harness(self.directory, self)
                if custom:
                    node._topo_doc.seed_actions({'custom': {'composable': False}},
                                               {'custom_bt': '<custom/>'})
                original = copy.deepcopy(node._topo_doc.to_dict())
                actions = original['actions'] if custom else default_actions()
                definitions = original['definitions'] if custom else default_definitions()
                old_doc = node._topo_doc
                persist_and_reload(
                    node, lambda doc: doc.insert_node(TopoNode(name='NEW', x=0.0, y=0.0)))
                saved = parse_topo_yaml(self.directory / 'live')
                self.assertEqual(saved.actions, actions)
                self.assertEqual(saved.definitions, definitions)
                self.assertEqual({entry.name for entry in saved.nodes}, {'A', 'NEW'})
                self.assertEqual(old_doc.to_dict(), original)
                self.assertIn('updated', node._run_vm.drop_node.status)
                node._topo_map_pub.publish.assert_called_once()
                (self.directory / 'live').unlink()
