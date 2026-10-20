"""Sim and System pages, and the tools card, composed over fake UI elements."""

# pylint: disable=protected-access
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

# Imported before the patched block so the cards and every other test module share one registry.
from devkit_ui import process_registry  # noqa: F401  # pylint: disable=unused-import
from test_run_ui import FakeElement, fake_ui


class PageUi:
    """The shared fake UI plus a column that pages can subclass, as they do with nicegui's."""

    column = FakeElement

    def __getattr__(self, name):
        return getattr(fake_ui, name)

    def select(self, *args, **kwargs):
        """Return a generic selector element."""
        return fake_ui.element('select', *args, **kwargs)

    def slider(self, *args, **kwargs):
        """Return a generic slider element."""
        return fake_ui.element('slider', *args, **kwargs)

    def upload(self, *args, **kwargs):
        """Return a generic upload element."""
        return fake_ui.element('upload', *args, **kwargs)

    def leaflet(self, *args, **kwargs):
        """Return a map element that accepts the calls the GPS card makes."""
        element = fake_ui.element('leaflet', *args, **kwargs)
        element.center = kwargs.get('center')
        element.run_map_method = Mock()
        element.set_center = Mock()
        element.marker = Mock()
        return element


page_ui = PageUi()
REQUEST = SimpleNamespace(url=SimpleNamespace(hostname='robot.local'))
page_nicegui = ModuleType('nicegui')
page_nicegui.ui = page_ui
page_nicegui.run = SimpleNamespace()
packages = ModuleType('ament_index_python.packages')
packages.PackageNotFoundError = type('PackageNotFoundError', (LookupError,), {})
packages.get_package_share_directory = Mock(return_value='/ros/topo')

_modules_before = set(sys.modules)
with patch.dict(sys.modules, {'nicegui': page_nicegui, 'ament_index_python.packages': packages}):
    from devkit_ui.pages.sim import gazebo_card, sim_page
    from devkit_ui.pages.sim.sim_page import SimPage
    from devkit_ui.pages.system import system_page, tools_card
    from devkit_ui.pages.system.system_page import SystemPage
    from devkit_ui.pages.system.tools_card import ToolsCard
    _imported_here = set(sys.modules) - _modules_before

# Leaving the block discards these modules, but a surviving parent package keeps them as
# attributes, and a later `from package import module` would then return the stale copy.
for _name in _imported_here:
    _parent, _, _child = _name.rpartition('.')
    if _parent in sys.modules and hasattr(sys.modules[_parent], _child):
        delattr(sys.modules[_parent], _child)


def card_titles():
    """Return the card titles rendered so far, in order."""
    return [e.text for e in fake_ui.elements
            if e.kind == 'label' and e.class_names == 'font-semibold mb-2']


class TestSimPage(unittest.TestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(gazebo_card, 'request_host', return_value='robot.local'))
        self.sim_vm = Mock()
        self.sim_vm.list_crop_models.return_value = ['plant']
        self.topo_vm = Mock()

    def test_cards_render_in_workflow_order(self):
        """Verify assets, launch, row follow, soil texture and archive appear in that order."""
        SimPage(self.sim_vm, self.topo_vm)
        self.assertEqual(
            card_titles(),
            ['Plant Placement', 'Gazebo Sim', 'Sowbot Row Follow', 'Soil Texture', 'Map Archive'])

    def test_page_is_a_full_width_column(self):
        """Verify the page itself is styled as a full-width column."""
        # NOTE: The patched ui.column supplies FakeElement.class_names at runtime.
        self.assertEqual(
            SimPage(self.sim_vm, self.topo_vm).class_names,  # pylint: disable=no-member
            'w-full gap-3')

    def test_cards_get_the_view_models_and_live_plant_settings_they_need(self):
        """Verify each card is built on the right view model and every card is full width."""
        names = ('PlantPlacementCard', 'GazeboCard', 'RowFollowCard', 'SoilTextureCard',
                 'MapArchiveCard')
        manager = Mock()
        for name in names:
            self.enterContext(patch.object(sim_page, name, getattr(manager, name)))
        SimPage(self.sim_vm, self.topo_vm)
        placement = manager.PlantPlacementCard.return_value.classes.return_value
        manager.PlantPlacementCard.assert_called_once_with(self.sim_vm)
        manager.GazeboCard.assert_called_once_with(self.sim_vm, placement.settings)
        manager.RowFollowCard.assert_called_once_with()
        manager.SoilTextureCard.assert_called_once_with(self.sim_vm)
        manager.MapArchiveCard.assert_called_once_with(self.topo_vm)
        for name in names:
            getattr(manager, name).return_value.classes.assert_called_once_with('w-full')


class TestSystemPage(unittest.TestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(tools_card, 'request_host', return_value='robot.local'))
        self.enterContext(patch.object(fake_ui.context.client, 'request', REQUEST, create=True))
        self.telemetry = SimpleNamespace(
            battery_text='', linear_velocity=0.0, angular_velocity=0.0, gps=None,
            bumper_front_top_active=False, bumper_front_bottom_active=False,
            bumper_back_active=False, estop_front_active=False, estop_back_active=False)
        self.brain = Mock()
        self.system = Mock()
        self.system.status.return_value = None

    def test_cards_render_health_first_then_maintenance(self):
        """Verify odometry, battery and safety come before ESP, GPS and tools."""
        SystemPage(self.telemetry, self.brain, self.system)
        self.assertEqual(card_titles(), ['Odometry', 'Battery', 'Safety', 'ESP', 'GPS', 'Tools'])

    def test_health_cards_share_one_stretched_row(self):
        """Verify odometry, battery and safety are built inside the same full-width row."""
        page = SystemPage(self.telemetry, self.brain, self.system)
        # NOTE: The patched ui.column supplies FakeElement.children at runtime.
        rows = [child for child in page.children if child.kind == 'row']  # pylint: disable=no-member
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].class_names, 'items-stretch w-full gap-3')
        # NOTE: The patched ui.column supplies FakeElement.class_names at runtime.
        self.assertEqual(page.class_names, 'w-full gap-3')  # pylint: disable=no-member

    def test_cards_get_the_view_models_they_need_and_the_right_widths(self):
        """Verify telemetry, brain and system view models reach their cards and widths are set."""
        flex = ('OdometryCard', 'BatteryCard', 'SafetyCard')
        full = ('EspCard', 'GpsCard', 'ToolsCard')
        manager = Mock()
        for name in flex + full:
            self.enterContext(patch.object(system_page, name, getattr(manager, name)))
        SystemPage(self.telemetry, self.brain, self.system)
        for name in ('OdometryCard', 'BatteryCard', 'SafetyCard', 'GpsCard'):
            getattr(manager, name).assert_called_once_with(self.telemetry)
        manager.EspCard.assert_called_once_with(self.brain)
        manager.ToolsCard.assert_called_once_with(self.system)
        for name in flex:
            getattr(manager, name).return_value.classes.assert_called_once_with('flex-1')
        for name in full:
            getattr(manager, name).return_value.classes.assert_called_once_with('w-full')


class TestToolsCardLayout(unittest.TestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(tools_card, 'request_host', return_value='robot.local'))
        self.enterContext(patch.object(fake_ui.context.client, 'request', REQUEST, create=True))
        system = Mock()
        system.status.return_value = None
        self.card = ToolsCard(system)

    def test_each_tool_has_start_and_stop_controls_separated_by_rules(self):
        """Verify the four tool groups are separated by three rules and each has a stop control."""
        buttons = [e.text for e in fake_ui.elements if e.kind == 'button']
        self.assertEqual(sum(e.kind == 'separator' for e in fake_ui.elements), 3)
        self.assertEqual(sum(text.startswith('Stop') for text in buttons), 4)
        self.assertEqual(sum(text.startswith(('Start', 'Launch')) for text in buttons), 4)

    def test_title_is_the_first_child_and_controls_share_one_wrapping_row(self):
        """Verify the card title precedes a single wrapping row holding every control."""
        # NOTE: The patched ui.card supplies FakeElement.children at runtime.
        title, row = self.card.children  # pylint: disable=no-member
        self.assertEqual((title.text, row.kind), ('Tools', 'row'))
        self.assertIn('flex-wrap', row.class_names)


if __name__ == '__main__':
    unittest.main()
