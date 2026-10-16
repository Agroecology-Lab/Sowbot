"""A small fake nicegui.ui, and the Plan cards imported over it, for the Plan card tests."""
# pylint: disable=keyword-arg-before-vararg
import asyncio
import sys
from collections.abc import Callable
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from devkit_ui.plan_types import PlannedRows
from devkit_ui.view_models.plan_view_model import PlanView


class Element:
    """A UI element that records what the cards ask of it. Subclassed by the other fakes."""

    def __init__(self, kind='element', text='', *args, **kwargs) -> None:
        self.kind = kind
        self.text = text
        self.args = args
        self.kwargs = kwargs
        self.value = kwargs.get('value')
        self.click = kwargs.get('on_click')
        self.enabled = True
        self.visible = True
        self.class_names = ''
        self.styles = []
        self.handlers = {}
        self.children = []
        self.tooltip_text = None
        fake_ui.add(self)

    def __enter__(self):
        fake_ui.context.append(self)
        return self

    def __exit__(self, *_args) -> None:
        fake_ui.context.pop()

    def classes(self, value):
        self.class_names = value
        return self

    def props(self, _value):
        return self

    def style(self, value):
        self.styles.append(value)
        return self

    def tooltip(self, text):
        self.tooltip_text = text
        return self

    def on(self, event, handler):
        self.handlers[event] = handler

    def on_value_change(self, handler):
        self.handlers['value'] = handler

    def set_text(self, text):
        self.text = text

    def set_enabled(self, enabled):
        self.enabled = enabled

    def set_visibility(self, visible):
        self.visible = visible

    def clear(self):
        self.children.clear()


class Layer(Element):
    """A map layer."""


class Leaflet(Element):
    """A map that remembers the layers on it."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__('leaflet', '', *args, **kwargs)
        self.center = kwargs.get('center')
        self.layers = []
        self.removed = []
        self.run_map_method = Mock()
        self.tile_layer = Mock()
        self.set_center = Mock()

    def marker(self, latlng):
        """Add a marker layer."""
        layer = Layer('marker', latlng=latlng)
        self.layers.append(layer)
        return layer

    def generic_layer(self, name, args):
        """Add a polygon or polyline layer."""
        layer = Layer(name, args=args)
        self.layers.append(layer)
        return layer

    def remove_layer(self, layer):
        """Remove a layer, as nicegui does."""
        self.layers.remove(layer)
        self.removed.append(layer)

    def layers_named(self, kind):
        """Return the layers of a kind that are still on the map."""
        return [layer for layer in self.layers if layer.kind == kind]


class Dialog(Element):
    """A dialog that is answered by the test's hook when awaited."""

    def __init__(self) -> None:
        super().__init__('dialog')
        self.submitted = []

    def submit(self, result):
        """Record the button the user pressed."""
        self.submitted.append(result)

    def __await__(self):
        answer_dialog = fake_ui.answer_dialog
        assert answer_dialog is not None
        return answer_dialog(self)
        yield  # pylint: disable=unreachable


class FakeUi:
    """The few nicegui.ui functions the Plan cards call."""
    card = Element
    column = Element
    dialog = Dialog

    def __init__(self) -> None:
        self.context = []
        self.elements = []
        self.timers = []
        self.answer_dialog: Callable[[Dialog], str] | None = None

    def reset(self) -> None:
        """Forget everything built so far."""
        self.context.clear()
        self.elements.clear()
        self.timers.clear()

    def add(self, element) -> None:
        """Register an element and attach it to the open container."""
        self.elements.append(element)
        if self.context and self.context[-1] is not element:
            self.context[-1].children.append(element)

    def find(self, kind, text=None):
        """Return the elements of a kind, optionally with exactly this text."""
        return [e for e in self.elements if e.kind == kind and (text is None or e.text == text)]

    def button(self, text='', *args, **kwargs):
        """Create a button."""
        return Element('button', text, *args, **kwargs)

    def timer(self, interval, callback):
        """Record a timer instead of starting one."""
        self.timers.append((interval, callback))

    def leaflet(self, *args, **kwargs):
        """Create a map."""
        return Leaflet(*args, **kwargs)

    def __getattr__(self, name):
        return lambda *args, **kwargs: Element(name, *args, **kwargs)


__all__ = [
    'CORNERS', 'FORM', 'GREEN', 'GREY', 'RED', 'BoundaryMapCard', 'Deferred', 'MissionQueueCard',
    'MissionStoreCard', 'PlanControlsCard', 'PlanPage', 'fake_ui', 'good', 'mission_queue_card',
    'mission_store_card', 'plan_controls_card', 'plan_page', 'planned', 'repair_dialog',
    'run_to_end', 'texts', 'walk',
]

fake_ui = FakeUi()
fake_nicegui = ModuleType('nicegui')
fake_nicegui.ui = fake_ui
fake_nicegui.run = SimpleNamespace(io_bound=None)

_modules_before = set(sys.modules)
with patch.dict(sys.modules, {'nicegui': fake_nicegui}):
    from devkit_ui.pages.plan import (
        mission_queue_card,
        mission_store_card,
        plan_controls_card,
        plan_page,
        repair_dialog,
    )
    from devkit_ui.pages.plan.boundary_map_card import BoundaryMapCard
    from devkit_ui.pages.plan.mission_queue_card import MissionQueueCard
    from devkit_ui.pages.plan.mission_store_card import MissionStoreCard
    from devkit_ui.pages.plan.plan_controls_card import PlanControlsCard
    from devkit_ui.pages.plan.plan_page import PlanPage
    _imported_here = set(sys.modules) - _modules_before

# Leaving the block discards these modules, but a surviving parent package keeps them as
# attributes, and a later `from package import module` would then return the stale copy.
for _name in _imported_here:
    _parent, _, _child = _name.rpartition('.')
    if _parent in sys.modules and hasattr(sys.modules[_parent], _child):
        delattr(sys.modules[_parent], _child)

CORNERS = ((51.0, -2.0), (51.003, -2.0), (51.0, -1.999))
GREEN, RED, GREY = 'color:#1a7f37', 'color:#cf222e', 'color:#57606a'


class Deferred:
    """Suspend a worker until the test supplies its result, without timing or threads."""

    def __await__(self):
        """Yield control and return the view supplied by the test."""
        return (yield)


def planned(tag=51.0) -> PlannedRows:
    """Return one swath, distinguishable by tag."""
    return PlannedRows([[(tag, -2.0), (tag + 0.001, -2.0)]], (tag, -2.0))


def good(tag=51.0) -> PlanView:
    """Return a successful planning view."""
    return PlanView(True, 'planned rows', planned(tag))


def run_to_end(coro):
    """Run a coroutine that never waits on a worker."""
    return asyncio.run(coro)


FORM = {'tool_width': 1.2, 'angle_deg': 0, 'headland_m': 0, 'snake': True, 'obstacle_pad_m': 0,
        'contour': False, 'recon_path': '', 'dem_res': 1.0}


def walk(element):
    """Yield an element and everything built inside it, in order."""
    yield element
    for child in element.children:
        yield from walk(child)


def texts(element, kind='label'):
    """Return the text of every element of a kind inside an element."""
    return [e.text for e in walk(element) if e.kind == kind]
