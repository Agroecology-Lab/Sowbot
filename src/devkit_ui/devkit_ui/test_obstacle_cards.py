"""Exercise obstacle card callbacks without a browser or ROS runtime."""
# pylint: disable=protected-access

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from devkit_ui.application_services.obstacle_application_service import (
    ObstacleApplicationService,
)
from devkit_ui.test_run_ui import FakeElement, FakeUi, nicegui
from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel

with patch.dict(sys.modules, {'nicegui': nicegui}):
    from devkit_ui.pages import obstacle_cards


class MissionUi(FakeUi):
    """Extend the shared UI double with visibility and deferred click registration."""

    def column(self, *args, **kwargs):
        element = super().column(*args, **kwargs)
        element.set_visibility = Mock()
        return element

    def button(self, *args, **kwargs):
        element = super().button(*args, **kwargs)
        if element.on_click is None:
            element.on_click = Mock()
        return element


class TestObstacleCards(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = MissionUi()
        self.enterContext(patch.object(FakeElement, 'ui', self.ui))
        self.ui.card = FakeElement
        self.enterContext(patch.object(obstacle_cards, 'ui', self.ui))
        self.manager = Mock(obstacles=(), version=0, status='')
        telemetry = SimpleNamespace(latest_gps=SimpleNamespace(latitude=51.0, longitude=-1.0))
        self.vm = ObstacleViewModel(ObstacleApplicationService(self.manager, telemetry))
        self.mission_map = Mock()
        self.mission_map.generic_layer.side_effect = lambda **_kwargs: Mock()
        self.mission_map.marker.side_effect = lambda **_kwargs: Mock()
        self.boundary_click = Mock()

    def test_map_clicks_follow_mode_and_shape_controls(self) -> None:
        handle = self.attach_sidebar()
        controls = self.ui.find('column')
        controls.set_visibility.assert_called_once_with(False)
        self.click(51.0, -1.0)
        self.boundary_click.assert_called_once_with(51.0, -1.0)
        self.manager.add.assert_not_called()

        self.select_mode('obstacle')
        controls.set_visibility.assert_called_with(True)
        self.vm.default_radius = 1.25
        self.click(52.0, -2.0)
        self.manager.add.assert_called_once_with('circle', lat=52.0, lon=-2.0, radius_m=1.25)

        self.select_shape('polygon')
        self.click(53.0, -3.0)
        self.assertEqual(handle.corners, [(53.0, -3.0)])
        self.mission_map.marker.assert_called_once_with(latlng=(53.0, -3.0))
        self.mission_map.generic_layer.assert_not_called()
        self.manager.add.assert_called_once()

        self.select_mode('boundary')
        controls.set_visibility.assert_called_with(False)
        self.click(54.0, -4.0)
        self.boundary_click.assert_called_with(54.0, -4.0)
        self.assertEqual(handle.corners, [(53.0, -3.0)])

    def test_polygon_preview_replaces_previous_layer(self) -> None:
        handle = self.attach_sidebar()
        self.select_mode('obstacle')
        self.select_shape('polygon')
        self.click(51.0, -1.0)
        self.click(51.1, -1.0)
        previous = handle.layer[0]
        self.click(51.1, -1.1)

        previous.run_method.assert_called_once_with('remove')
        self.assertEqual(self.mission_map.generic_layer.call_count, 2)
        layer_args = self.mission_map.generic_layer.call_args.kwargs
        self.assertEqual(layer_args['name'], 'polygon')
        self.assertEqual(layer_args['args'][0], [[51.0, -1.0], [51.1, -1.0], [51.1, -1.1]])
        self.assertEqual(len(handle.markers), 3)

    def test_finish_keeps_rejected_polygon_then_clears_on_retry(self) -> None:
        handle = self.attach_sidebar()
        self.select_mode('obstacle')
        self.select_shape('polygon')
        for lat, lon in [(51.0, -1.0), (51.1, -1.0), (51.1, -1.1)]:
            self.click(lat, lon)
        corners = list(handle.corners)
        layer, markers = handle.layer[0], list(handle.markers)
        finish = self.ui.find('button', 'Finish polygon').on_click.call_args.args[0]
        self.manager.add.return_value = None

        finish()

        self.manager.add.assert_called_once_with('polygon', points_ll=corners)
        submitted = self.manager.add.call_args.kwargs['points_ll']
        self.assertIsNot(submitted, handle.corners)
        self.assertEqual(handle.corners, corners)
        layer.run_method.assert_not_called()
        for marker in markers:
            marker.run_method.assert_not_called()

        self.manager.add.return_value = 'OBS_1'
        finish()

        self.assertEqual(handle.corners, [])
        self.assertEqual(handle.markers, [])
        self.assertIsNone(handle.layer[0])
        self.assertEqual(submitted, corners)
        self.assertEqual(self.manager.add.call_args.kwargs['points_ll'], corners)
        for item in [layer, *markers]:
            item.run_method.assert_called_once_with('remove')

    def test_finish_rejects_incomplete_polygon_without_discarding_corners(self) -> None:
        handle = self.attach_sidebar()
        self.select_mode('obstacle')
        self.select_shape('polygon')
        self.click(51.0, -1.0)
        self.ui.find('button', 'Finish polygon').on_click.call_args.args[0]()
        self.manager.add.assert_not_called()
        self.manager.set_status.assert_called_once_with('ERROR: polygon needs 3+ corners')
        self.assertEqual(handle.corners, [(51.0, -1.0)])
        handle.markers[0].run_method.assert_not_called()

    def test_clear_continues_after_removed_layer_errors_and_is_repeatable(self) -> None:
        handle = obstacle_cards.MissionDrawHandle(self.vm, self.mission_map, Mock())
        broken, healthy = Mock(), Mock()
        broken.run_method.side_effect = RuntimeError('already removed')
        handle.corners.append((51.0, -1.0))
        handle.layer[0] = broken
        handle.markers.extend([broken, healthy])

        handle.clear_in_progress()
        handle.clear_in_progress()

        self.assertEqual((handle.corners, handle.markers, handle.layer), ([], [], [None]))
        healthy.run_method.assert_called_once_with('remove')

    def test_nav_and_mission_radius_controls_bind_to_same_view_model(self) -> None:
        obstacle_cards.attach_nav_card(self.vm)
        handle = self.attach_sidebar()
        radius_inputs = [
            element
            for element in self.ui.elements
            if element.kind == 'number' and element.binding is not None
        ]
        self.assertEqual(len(radius_inputs), 2)
        for element in radius_inputs:
            self.assertIs(element.binding.source, self.vm)
            self.assertEqual(element.binding.attribute, 'default_radius')
        self.assertEqual(handle.obstacle_pad.value, 0.5)
        self.assertIsNone(handle.obstacle_pad.binding)

    def test_panel_renders_shapes_and_delete_buttons_capture_each_name(self) -> None:
        self.manager.obstacles = (
            {
                'name': 'GATE',
                'type': 'circle',
                'center': {'lat': 51.0, 'lon': -1.0},
                'radius_m': 1.5,
            },
            {
                'name': 'DITCH',
                'type': 'polygon',
                'points': [
                    {'lat': 51.0, 'lon': -1.0},
                    {'lat': 51.1, 'lon': -1.0},
                    {'lat': 51.1, 'lon': -1.1},
                ],
            },
        )
        handle, refresh = self.attach_panel()
        refresh()

        self.ui.find('label', '2 saved')
        self.assertEqual(set(handle.rendered), {'GATE', 'DITCH'})
        circle, polygon = self.mission_map.generic_layer.call_args_list
        self.assertEqual(circle.kwargs['name'], 'circle')
        self.assertEqual(circle.kwargs['args'][0], [51.0, -1.0])
        self.assertEqual(circle.kwargs['args'][1]['radius'], 1.5)
        self.assertEqual(polygon.kwargs['name'], 'polygon')
        self.assertEqual(polygon.kwargs['args'][0], [[51.0, -1.0], [51.1, -1.0], [51.1, -1.1]])
        for button in [e for e in self.ui.elements if e.kind == 'button' and e.text == '✕']:
            button.on_click(None)
        self.assertEqual(self.manager.delete.call_args_list, [call('GATE'), call('DITCH')])

    def test_panel_refreshes_status_independently_and_clears_deleted_layers(self) -> None:
        self.manager.obstacles = (
            {
                'name': 'GATE',
                'type': 'circle',
                'center': {'lat': 51.0, 'lon': -1.0},
                'radius_m': 0.5,
            },
        )
        handle, refresh = self.attach_panel()
        refresh()
        layer = handle.rendered['GATE']
        list_column = self.ui.find('column')
        children = list(list_column.children)
        status = next(e for e in self.ui.elements if e.kind == 'label' and e.text == '')
        with patch.object(status, 'set_text', wraps=status.set_text) as set_text:
            refresh()
            set_text.assert_not_called()
            self.manager.status = 'ERROR: disk full'
            refresh()
            set_text.assert_called_once_with('ERROR: disk full')
        self.assertEqual(status.styles[-1], 'color:#cf222e')
        self.assertEqual(list_column.children, children)
        self.mission_map.generic_layer.assert_called_once()
        layer.run_method.assert_not_called()

        self.manager.obstacles = ()
        self.manager.version += 1
        self.manager.status = 'deleted GATE'
        refresh()

        layer.run_method.assert_called_once_with('remove')
        self.assertEqual(handle.rendered, {})
        self.assertEqual([e.text for e in list_column.children], ['No obstacles yet'])
        self.ui.find('label', '0 saved')
        self.assertEqual(status.styles[-1], 'color:#1a7f37')

    def test_rejected_mark_keeps_name_and_does_not_offer_undo(self) -> None:
        self.manager.add.return_value = None
        obstacle_cards.attach_nav_card(self.vm)
        name = self.ui.find('input')
        name.value = 'duplicate'
        self.ui.find('button', 'Mark here').on_click()
        self.assertEqual(name.value, 'duplicate')
        self.assertFalse(any(e.kind == 'dialog' for e in self.ui.elements))
        self.assertFalse(any(timer.once for timer in self.ui.timers))

    def test_nav_status_polling_handles_error_success_and_clearing(self) -> None:
        obstacle_cards.attach_nav_card(self.vm)
        status = self.ui.find('label', '')
        refresh = self.ui.timers[-1].callback
        for message, color in [('ERROR: no fix', '#cf222e'), ('saved', '#1a7f37'), ('', '#57606a')]:
            with self.subTest(message=message):
                self.manager.status = message
                refresh()
                self.assertEqual(status.text, message)
                self.assertEqual(status.styles[-1], f'color:{color}')
                with patch.object(status, 'set_text') as set_text:
                    refresh()
                    set_text.assert_not_called()

    def test_drawing_handles_do_not_share_in_progress_state(self) -> None:
        first = obstacle_cards.MissionDrawHandle(self.vm, self.mission_map, Mock())
        second = obstacle_cards.MissionDrawHandle(self.vm, self.mission_map, Mock())
        first.corners.append((51.0, -1.0))
        first.markers.append(Mock())
        first.layer[0] = Mock()
        first.rendered['GATE'] = Mock()
        first.draw_mode[0] = 'obstacle'
        first.obs_shape[0] = 'polygon'
        self.assertEqual((second.corners, second.markers, second.layer), ([], [], [None]))
        self.assertEqual(second.rendered, {})
        self.assertEqual((second.draw_mode, second.obs_shape), (['boundary'], ['circle']))

    def attach_sidebar(self):
        return obstacle_cards.attach_mission_sidebar_controls(
            self.vm, self.mission_map, self.boundary_click
        )

    def attach_panel(self):
        handle = obstacle_cards.MissionDrawHandle(self.vm, self.mission_map, Mock())
        obstacle_cards.attach_mission_obstacle_panel(handle)
        return handle, self.ui.timers[-1].callback

    def select_mode(self, mode):
        toggles = [e for e in self.ui.elements if e.kind == 'toggle']
        toggles[0].handlers['update:model-value'](SimpleNamespace(args=mode))

    def select_shape(self, shape):
        toggles = [e for e in self.ui.elements if e.kind == 'toggle']
        toggles[1].handlers['update:model-value'](SimpleNamespace(args=shape))

    def click(self, lat, lon):
        self.mission_map.on.call_args.args[1](
            SimpleNamespace(args={'latlng': {'lat': lat, 'lng': lon}})
        )


class TestRemoveLayer(unittest.TestCase):
    def test_removes_the_layer_through_leaflet(self) -> None:
        """Verify a healthy layer receives the remove call."""
        layer = Mock()
        obstacle_cards._remove_layer(layer)
        layer.run_method.assert_called_once_with('remove')

    def test_a_failing_remove_is_logged_not_raised(self) -> None:
        """Verify a layer whose client is gone does not break the caller, and the failure is logged."""
        layer = Mock()
        layer.run_method.side_effect = RuntimeError('client gone')
        with self.assertLogs(obstacle_cards.__name__, level='DEBUG') as logs:
            obstacle_cards._remove_layer(layer)
        self.assertIn('could not remove map layer', logs.output[0])


if __name__ == '__main__':
    unittest.main()
