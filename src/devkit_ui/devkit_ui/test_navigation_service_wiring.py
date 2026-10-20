"""Exercise the node's tab composition with isolated ROS and UI boundaries."""
# pylint: disable=exec-used,protected-access,attribute-defined-outside-init
import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from devkit_ui.test_run_ui import fake_ui


def load_navigation_service_harness():
    """Load the real methods, following the existing navigation test harness pattern."""
    source = Path(__file__).with_name('ui_node.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node_class = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'NiceGuiNode')
    names = {'content'}
    members = [node for node in node_class.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    harness = ast.ClassDef(name='Harness', bases=[], keywords=[], body=members, decorator_list=[])
    namespace = {name: Mock() for name in ('NavPage', 'SimPage', 'SystemPage')}
    namespace.update(ui=fake_ui, _APP_CSS='')
    module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
    exec(compile(module, source, 'exec'), namespace)
    return namespace['Harness'](), namespace


class TestUiTabs(unittest.TestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        for kind in ('tabs', 'tab', 'tab_panels', 'tab_panel'):
            self.enterContext(patch.object(
                fake_ui, kind,
                side_effect=lambda *args, _kind=kind, **kwargs: fake_ui._element(
                    _kind, *args, **kwargs), create=True))
        self.head = self.enterContext(patch.object(fake_ui, 'add_head_html', create=True))
        self.node, self.namespace = load_navigation_service_harness()
        for name in ('_plan_content', '_sim_vm', '_global_vm', '_run_vm', '_topo_vm',
                     '_telemetry_vm', '_obstacle_vm', '_authoring_vm', '_robot_brain_vm',
                     '_system_vm'):
            setattr(self.node, name, Mock())

    def test_tabs_start_on_run_and_pass_correct_view_models_to_pages(self):
        """Verify the four tabs, Run first, and each page receives its own view models."""
        self.node.content()
        tabs = [element for element in fake_ui.elements if element.kind == 'tab']
        self.assertEqual([tab.text for tab in tabs], ['Run', 'Plan', 'Sim', 'System'])
        self.assertIs(fake_ui.find('tab_panels').value, tabs[0])
        panels = [element for element in fake_ui.elements if element.kind == 'tab_panel']
        self.assertEqual([panel.text for panel in panels], tabs)
        self.namespace['NavPage'].assert_called_once_with(
            global_vm=self.node._global_vm, run_vm=self.node._run_vm,
            topo_vm=self.node._topo_vm, telemetry_vm=self.node._telemetry_vm,
            obstacle_vm=self.node._obstacle_vm, authoring=self.node._authoring_vm)
        self.node._plan_content.assert_called_once_with()
        self.namespace['SimPage'].assert_called_once_with(self.node._sim_vm, self.node._topo_vm)
        self.namespace['SystemPage'].assert_called_once_with(
            telemetry_vm=self.node._telemetry_vm, robot_brain_vm=self.node._robot_brain_vm,
            system_vm=self.node._system_vm)
        self.head.assert_not_called()


if __name__ == '__main__':
    unittest.main()
