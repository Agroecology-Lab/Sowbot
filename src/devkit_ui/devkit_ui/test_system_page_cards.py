# pylint: disable=protected-access,import-outside-toplevel
"""System page cards and the view models behind the System and Sim pages."""
import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from devkit_ui.test_run_ui import fake_ui, nicegui

with patch.dict(sys.modules, {'nicegui': nicegui}):
    from devkit_ui.pages.sim.map_archive_card import MapArchiveCard
    from devkit_ui.pages.sim.plant_placement_card import PlantPlacementCard, PlantSettings
    from devkit_ui.pages.system.battery_card import BatteryCard
    from devkit_ui.pages.system.esp_card import EspCard
    from devkit_ui.pages.system.safety_card import SafetyCard
from devkit_ui.view_models.robot_brain_view_model import RobotBrainViewModel
from devkit_ui.view_models.sim_view_model import SimAssetError, SimViewModel


class TestRobotBrainViewModel(unittest.TestCase):
    def test_each_command_reaches_the_application_service_once(self):
        """Verify every ESP command is delegated to its matching service method."""
        service = Mock()
        brain = RobotBrainViewModel(service)
        for command in ('enable', 'disable', 'reset', 'restart', 'configure'):
            with self.subTest(command=command):
                getattr(brain, command)()
                getattr(service, command).assert_called_once_with()


class TestSimViewModel(unittest.TestCase):
    def test_asset_calls_are_delegated_with_their_arguments(self):
        """Verify the view model forwards each asset call, including the collision flag."""
        assets = Mock()
        assets.list_crop_models.return_value = ['plant']
        assets.has_crop_model.return_value = True
        assets.create_crop_model.return_value = 'maize'
        assets.import_soil_zip.return_value = ['albedo.png']
        assets.classify_map.return_value = 'albedo'
        world = Mock()
        world.rebuild.return_value = '12'
        sim = SimViewModel(assets, world)

        self.assertEqual(sim.list_crop_models(), ['plant'])
        self.assertTrue(sim.has_crop_model('plant'))
        sim.validate_mesh(b'glTF', 'a.glb', collision=True)
        self.assertEqual(sim.create_crop_model('maize', b'v', b'c'), 'maize')
        self.assertEqual(sim.import_soil_zip(b'zip'), ['albedo.png'])
        self.assertEqual(sim.classify_map('albedo.png'), 'albedo')
        self.assertEqual(sim.rebuild_world(0.5, 0.75, 1.0, 'maize', 'plant', 20), '12')
        world.rebuild.assert_called_once_with(0.5, 0.75, 1.0, 'maize', 'plant', 20)

        assets.validate_mesh.assert_called_once_with(b'glTF', 'a.glb', collision=True)
        assets.create_crop_model.assert_called_once_with('maize', b'v', b'c')

    def test_rejections_propagate_as_the_reexported_error(self):
        """Verify pages can catch SimAssetError imported from the view model module."""
        assets = Mock()
        assets.validate_mesh.side_effect = SimAssetError('invalid glTF file')
        with self.assertRaisesRegex(SimAssetError, 'invalid glTF file'):
            SimViewModel(assets, Mock()).validate_mesh(b'x', 'a.glb')


class TestPlantSettings(unittest.TestCase):
    @staticmethod
    def inputs(**values):
        """Return stand-ins for the card's widgets holding the given values."""
        widgets = {'spacing': 100, 'row_width': 80, 'scale': 100, 'category': 'all',
                   'model': 'plant', 'weed_density': 10}
        widgets.update(values)
        return SimpleNamespace(**{f'_{name}': SimpleNamespace(value=value)
                                  for name, value in widgets.items()})

    def test_centimetres_and_percent_are_converted_for_worldgen(self):
        """Verify cm become metres, percent becomes a fraction and weed density stays a percent."""
        card = self.inputs(spacing=45, row_width=75, scale=250, category='weed',
                           model='maize', weed_density=35)
        self.assertEqual(PlantPlacementCard.settings(card),
                         PlantSettings(0.45, 0.75, 2.5, 'weed', 'maize', 35))

    def test_cleared_fields_fall_back_to_the_defaults(self):
        """Verify empty inputs give the default spacing, width, scale, category and model."""
        card = self.inputs(spacing=None, row_width=None, scale=None, category=None, model=None)
        self.assertEqual(PlantPlacementCard.settings(card),
                         PlantSettings(1.0, 0.8, 1.0, 'all', 'plant', 10))

    def test_zero_weed_density_is_kept_not_replaced_by_the_default(self):
        """Verify an explicit 0 % weeds is respected."""
        self.assertEqual(PlantPlacementCard.settings(self.inputs(weed_density=0)).weed_density, 0)


class TestEspCard(unittest.TestCase):
    def test_buttons_issue_their_own_command(self):
        """Verify each ESP button is wired to the matching robot brain command."""
        fake_ui.reset()
        brain = Mock()
        EspCard(brain)
        for label in ('Enable', 'Disable', 'Reset', 'Restart', 'Configure'):
            with self.subTest(label=label):
                fake_ui.find('button', label).on_click()
                getattr(brain, label.lower()).assert_called_once_with()
        self.assertEqual(len(brain.method_calls), 5)


class TestBatteryCard(unittest.TestCase):
    def test_text_follows_the_telemetry_view_model(self):
        """Verify the battery label is bound to the battery text property."""
        fake_ui.reset()
        telemetry = SimpleNamespace(battery_text='87.0%  24.1 V')
        BatteryCard(telemetry)
        label = next(e for e in fake_ui.elements if e.kind == 'label' and e.binding)
        self.assertEqual((label.binding.attribute, label.text), ('battery_text', '87.0%  24.1 V'))


class TestSafetyCard(unittest.TestCase):
    def setUp(self):
        """Build the card over flags that can be flipped per test."""
        fake_ui.reset()
        self.flags = SimpleNamespace(
            bumper_front_top_active=False, bumper_front_bottom_active=False,
            bumper_back_active=False, estop_front_active=False, estop_back_active=False)
        self.card = SafetyCard(self.flags)
        self.dots = [e for e in fake_ui.elements if e.kind == 'html' and 'dot-' in e.text]

    def test_polls_every_200_ms(self):
        """Verify the indicators refresh on a single 0.2 s timer."""
        self.assertEqual([(t.interval, t.callback) for t in fake_ui.timers],
                         [(0.2, self.card.refresh)])

    def test_idle_bumpers_are_ok_and_idle_estops_are_off(self):
        """Verify idle states use their own colours: bumpers ok, hardware e-stops off."""
        self.card.refresh()
        self.assertEqual([d.content for d in self.dots],
                         ['<span class="dot-ok"></span>'] * 3 + ['<span class="dot-off"></span>'] * 2)

    def test_active_flags_show_a_warning_on_only_their_own_dot(self):
        """Verify one active flag turns exactly its indicator to the warning state."""
        self.flags.bumper_back_active = True
        self.flags.estop_front_active = True
        self.card.refresh()
        warned = [i for i, d in enumerate(self.dots) if d.content == '<span class="dot-warn"></span>']
        self.assertEqual(warned, [2, 3])


class TestMapArchiveCard(unittest.TestCase):
    def setUp(self):
        """Build the card with recording callbacks."""
        fake_ui.reset()
        self.on_save = Mock(return_value='saved north_field')
        self.on_archive = Mock(return_value='archived as field_1')
        self.card = MapArchiveCard(lambda: 'field', self.on_save, self.on_archive)
        self.status = next(e for e in fake_ui.elements if e.kind == 'label' and e.text == '')
        self.name = fake_ui.find('input')

    def test_successful_save_reports_in_green_and_clears_the_name(self):
        """Verify a saved copy shows green status and empties the name field."""
        self.name.value = 'north_field'
        fake_ui.find('button', 'Save').on_click()
        self.on_save.assert_called_once_with('north_field')
        self.assertEqual((self.status.text, self.status.styles[-1]),
                         ('saved north_field', 'color:#1a7f37'))
        self.assertEqual(self.name.value, '')

    def test_failed_save_reports_in_red_and_keeps_the_name(self):
        """Verify an ERROR status is red and leaves the typed name for correction."""
        self.on_save.return_value = 'ERROR: name taken'
        self.name.value = 'north_field'
        fake_ui.find('button', 'Save').on_click()
        self.assertEqual((self.status.text, self.status.styles[-1]),
                         ('ERROR: name taken', 'color:#cf222e'))
        self.assertEqual(self.name.value, 'north_field')

    def test_archive_runs_only_after_confirmation(self):
        """Verify cancelling the dialog leaves the map alone and confirming archives it."""
        archive = fake_ui.find('button', 'Archive & Clear Map').on_click
        fake_ui.dialog_results[:] = ['cancel']
        asyncio.run(archive())
        self.on_archive.assert_not_called()

        fake_ui.dialog_results[:] = ['ok']
        asyncio.run(archive())
        self.assertEqual(self.on_archive.call_args_list, [call()])
        self.assertEqual(self.status.text, 'archived as field_1')


if __name__ == '__main__':
    unittest.main()
