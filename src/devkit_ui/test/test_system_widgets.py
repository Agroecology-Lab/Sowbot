"""GPS telemetry and shared tool-status widgets without ROS or a browser."""

# pylint: disable=protected-access
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_run_ui import fake_ui, nicegui

with patch.dict(sys.modules, {'nicegui': nicegui}):
    from devkit_ui.pages import widgets
    from devkit_ui.pages.system.gps_card import GpsCard
    from devkit_ui.pages.system.odometry_card import OdometryCard
    from devkit_ui.view_models.system_view_model import ToolStatusView


class TestToolWidgets(unittest.TestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(patch.object(widgets, 'ui', fake_ui))

    def test_request_host_handles_dns_ipv6_missing_host_and_html(self):
        for hostname, expected in (
            ('robot.local', 'robot.local'),
            ('2001:db8::1', '[2001:db8::1]'),
            (None, 'localhost'),
            ('x"<>&', 'x&quot;&lt;&gt;&amp;'),
        ):
            with self.subTest(hostname=hostname):
                request = SimpleNamespace(url=SimpleNamespace(hostname=hostname))
                with patch.object(fake_ui.context.client, 'request', request, create=True):
                    self.assertEqual(widgets.request_host(), expected)

    def test_show_status_applies_text_and_level_colour(self):
        for level, colour in (('ok', '#1a7f37'), ('error', '#cf222e'), ('muted', '#57606a')):
            with self.subTest(level=level):
                label = Mock()
                widgets.show_status(label, ToolStatusView(f'text {level}', level))
                label.set_text.assert_called_once_with(f'text {level}')
                label.style.assert_called_once_with(f'color:{colour}')

    def test_show_status_keeps_the_colour_without_a_level_and_ignores_none(self):
        label = Mock()
        widgets.show_status(label, ToolStatusView('stopping'))
        label.set_text.assert_called_once_with('stopping')
        label.style.assert_not_called()
        label = Mock()
        widgets.show_status(label, None)
        label.set_text.assert_not_called()
        label.style.assert_not_called()


class TestGpsCard(unittest.TestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.map = Mock(center=(48.0046, 3.6644))
        self.map.classes.return_value = self.map
        self.leaflet = self.enterContext(
            patch.object(fake_ui, 'leaflet', return_value=self.map, create=True)
        )
        self.telemetry = SimpleNamespace(gps=None)
        self.card = GpsCard(self.telemetry)

    def test_initial_map_uses_field_extent_and_refresh_timer(self):
        self.leaflet.assert_called_once_with(center=(48.0046, 3.6644), zoom=18)
        self.map.run_map_method.assert_called_once_with(
            'fitBounds', [[48.0031957, 3.6612233], [48.0060043, 3.6675767]]
        )
        self.map.marker.assert_called_once_with(latlng=self.map.center)
        self.assertEqual(
            [(t.interval, t.callback) for t in fake_ui.timers], [(2.0, self.card.refresh)]
        )

    def test_no_gps_preserves_existing_display(self):
        self.card._status.set_text('last fix')
        self.card.refresh()
        self.assertEqual(self.card._status.text, 'last fix')
        self.map.set_center.assert_not_called()
        self.map.marker.return_value.move.assert_not_called()

    def test_fix_labels_colours_coordinates_and_covariance_conversion(self):
        for code, label, colour in (
            (-1, 'NO FIX', '#cf222e'),
            (0, 'AUTONOMOUS', '#cf222e'),
            (1, 'SBAS', '#9a6700'),
            (2, 'DGNSS', '#9a6700'),
            (4, 'RTK FLOAT', '#9a6700'),
            (5, 'RTK FIXED', '#1a7f37'),
            (99, '99', '#9a6700'),
        ):
            with self.subTest(code=code):
                self.telemetry.gps = SimpleNamespace(
                    latitude=48.1,
                    longitude=-3.2,
                    altitude=12.34,
                    status=SimpleNamespace(status=code),
                    position_covariance=[0.0625] + [0] * 8,
                )
                self.card.refresh()
                self.map.set_center.assert_called_with((48.1, -3.2))
                self.map.marker.return_value.move.assert_called_with(48.1, -3.2)
                self.assertEqual(
                    self.card._status.text, f'{label}  48.100000, -3.200000  alt=12.3m  σ=0.25m'
                )
                self.assertEqual(self.card._status.styles[-1], f'color:{colour}')


class TestOdometryCard(unittest.TestCase):
    def test_readonly_sliders_track_their_own_velocity(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        with patch.object(
            fake_ui,
            'slider',
            side_effect=lambda **kw: fake_ui._element('slider', **kw),
            create=True,
        ):
            telemetry = SimpleNamespace(linear_velocity=0.4, angular_velocity=-0.2)
            OdometryCard(telemetry)
        sliders = [e for e in fake_ui.elements if e.kind == 'slider']
        self.assertEqual([s.value for s in sliders], [0.4, -0.2])
        self.assertEqual(
            [s.binding.attribute for s in sliders], ['linear_velocity', 'angular_velocity']
        )
        for slider in sliders:
            self.assertIn('readonly', slider.properties)
        telemetry.linear_velocity, telemetry.angular_velocity = -0.6, 0.7
        self.assertEqual([s.refresh_binding() for s in sliders], [-0.6, 0.7])


if __name__ == '__main__':
    unittest.main()
