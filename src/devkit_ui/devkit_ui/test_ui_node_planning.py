"""Exercise planning callbacks with deferred workers and isolated UI boundaries."""
# pylint: disable=exec-used,no-member,protected-access,attribute-defined-outside-init
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from devkit_ui.constants import FIELD27_BOUNDS, FIELD27_CENTER
from devkit_ui.plan_types import MIN_CORNERS, PlannedRows, PlanRequest


class Deferred:
    """Suspend a worker until the test supplies its result, without timing or threads."""

    def __await__(self):
        """Yield control and return the outcome supplied by the test."""
        return (yield)


def load_planning_harness():
    """Compile the real Plan controls through their final status timer."""
    source = Path(__file__).with_name('ui_node.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    node_class = next(n for n in tree.body
                      if isinstance(n, ast.ClassDef) and n.name == 'NiceGuiNode')
    method = next(n for n in node_class.body
                  if isinstance(n, ast.FunctionDef) and n.name == '_plan_content')
    end = next(i for i, n in enumerate(method.body)
               if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'timer')
    method.body = method.body[:end + 1]
    elements = {}

    def element(*args, **kwargs):
        """Keep labeled controls and preserve chaining on each UI element."""
        widget = MagicMock()
        widget.classes.return_value = widget
        widget.style.return_value = widget
        widget.props.return_value = widget
        widget.value = kwargs.get('value')
        if args:
            elements.setdefault(args[0], widget)
        return widget

    ui = Mock()
    for name in ('row', 'card', 'html', 'leaflet', 'number', 'slider', 'label',
                 'checkbox', 'input', 'separator', 'button', 'upload'):
        getattr(ui, name).side_effect = element
    sidebar = Mock(return_value=SimpleNamespace(
        obstacle_pad=SimpleNamespace(value=0), clear_in_progress=Mock()))
    namespace = {
        'ui': ui, 'FIELD27_CENTER': FIELD27_CENTER, 'FIELD27_BOUNDS': FIELD27_BOUNDS,
        'MIN_CORNERS': MIN_CORNERS, 'PlanRequest': PlanRequest,
        'attach_mission_sidebar_controls': sidebar,
        'ng_run': SimpleNamespace(io_bound=Mock(return_value=Deferred())),
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), source, 'exec'), namespace)
    node = SimpleNamespace(latest_gps=None, _plan_vm=Mock(), _obstacle_vm=Mock())
    namespace['_plan_content'](node)
    return node, elements, sidebar


class TestUiNodePlanning(unittest.TestCase):
    def setUp(self):
        """Build per-session controls and draw a complete field boundary."""
        self.node, self.elements, sidebar = load_planning_harness()
        self.map = sidebar.call_args.args[1]
        self.draw_corner = sidebar.call_args.args[2]
        self.plan_button = self.elements['Plan Rows']
        self.save_button = self.elements['Save as Topo Rows']
        self.clear = self.elements['Clear'].on_click.call_args.args[0]
        self.save = self.save_button.on_click.call_args.args[0]
        self.plan = self.plan_button.on_click.call_args.args[0]
        self.draw_boundary()
        self.outcome = SimpleNamespace(
            ok=True, status='planned rows',
            planned=PlannedRows([[(51.0, -2.0), (51.001, -2.0)]], (51.0, -2.0)))
        self.outcome.swaths = self.outcome.planned.swaths

    def draw_boundary(self):
        """Draw three corners through the actual map callback."""
        for corner in ((51.0, -2.0), (51.003, -2.0), (51.0, -1.999)):
            self.draw_corner(*corner)

    def start_plan(self):
        """Run planning until the worker awaits its result."""
        pending = self.plan()
        self.addCleanup(pending.close)
        pending.send(None)
        return pending

    def finish_plan(self, pending, outcome):
        """Deliver a worker result and require the callback to finish."""
        with self.assertRaises(StopIteration):
            pending.send(outcome)

    def test_current_result_is_rendered_and_saved(self):
        """A current result still draws rows and supplies the exact draft to Save."""
        pending = self.start_plan()
        self.map.generic_layer.reset_mock()
        self.finish_plan(pending, self.outcome)
        self.save()

        self.map.generic_layer.assert_called_once()
        self.assertIs(self.node._plan_vm.save_rows.call_args.args[0], self.outcome.planned)
        self.assertEqual(self.save_button.set_enabled.call_args.args, (True,))
        self.assertEqual(self.plan_button.set_enabled.call_args.args, (True,))

    def test_clear_discards_pending_success_and_failure(self):
        """Cleared work cannot restore rows, a draft, status, or enabled Save."""
        for outcome in (self.outcome, SimpleNamespace(ok=False, status='old failure')):
            with self.subTest(ok=outcome.ok):
                self.clear()
                self.draw_boundary()
                pending = self.start_plan()
                self.clear()
                self.map.generic_layer.reset_mock()
                self.save_button.set_enabled.reset_mock()
                self.plan_button.set_enabled.reset_mock()
                self.elements[''].set_text.reset_mock()
                self.finish_plan(pending, outcome)
                self.save()

                self.map.generic_layer.assert_not_called()
                self.save_button.set_enabled.assert_not_called()
                self.plan_button.set_enabled.assert_not_called()
                self.elements[''].set_text.assert_not_called()
                self.assertIsNone(self.node._plan_vm.save_rows.call_args.args[0])

    def test_old_result_cannot_enable_button_during_new_run(self):
        """Clear permits another run while its predecessor finishes harmlessly."""
        old = self.start_plan()
        self.clear()
        self.assertEqual(self.plan_button.set_enabled.call_args.args, (True,))
        self.draw_boundary()
        current = self.start_plan()
        self.finish_plan(old, self.outcome)
        self.assertEqual(self.plan_button.set_enabled.call_args.args, (False,))
        self.finish_plan(current, self.outcome)

    def test_old_result_cannot_replace_a_completed_new_run(self):
        """A late predecessor preserves the new draft and its rendered rows."""
        old = self.start_plan()
        self.clear()
        self.draw_boundary()
        current = self.start_plan()
        self.finish_plan(current, self.outcome)
        self.map.generic_layer.reset_mock()
        self.finish_plan(old, SimpleNamespace(ok=True, planned=None, swaths=[], status='old'))
        self.save()

        self.map.generic_layer.assert_not_called()
        self.assertIs(self.node._plan_vm.save_rows.call_args.args[0], self.outcome.planned)
