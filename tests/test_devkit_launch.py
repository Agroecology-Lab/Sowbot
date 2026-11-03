"""devkit.launch.py structure, with the ROS launch API replaced by recording stand-ins."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

LAUNCH_FILE = Path(__file__).parents[1] / 'src' / 'devkit_bringup' / 'launch' / 'devkit.launch.py'
ENV_KEYS = ('MAVLINK_ENDPOINT', 'MAP_ORIGIN_LAT', 'MAP_ORIGIN_LON', 'MAP_YAW_DEG',
            'GPS_PORT_ROVER', 'GPS_PORT_ROVER1', 'MCU_PORT', 'TMAP2_FILE')


class Recorded:
    """Keep the constructor arguments of any launch action or substitution."""

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


def _stub_modules() -> dict[str, ModuleType]:
    names = {
        'launch': ['LaunchDescription'],
        'launch.actions': ['DeclareLaunchArgument', 'ExecuteProcess', 'GroupAction', 'IncludeLaunchDescription',
                           'SetEnvironmentVariable', 'TimerAction', 'RegisterEventHandler', 'OpaqueFunction'],
        'launch.conditions': ['IfCondition', 'UnlessCondition'],
        'launch.launch_description_sources': ['PythonLaunchDescriptionSource'],
        'launch.substitutions': ['LaunchConfiguration', 'PythonExpression', 'PathJoinSubstitution'],
        'launch_ros.actions': ['Node'],
        'launch_ros.parameter_descriptions': ['ParameterValue'],
    }
    modules = {}
    for module_name, classes in names.items():
        module = ModuleType(module_name)
        for class_name in classes:
            setattr(module, class_name, type(class_name, (Recorded,), {}))
        modules[module_name] = module
    modules['launch'].LaunchDescription = lambda actions=(): list(actions)
    ament = ModuleType('ament_index_python.packages')
    ament.get_package_share_directory = lambda package: f'/share/{package}'
    modules['ament_index_python'] = ModuleType('ament_index_python')
    modules['ament_index_python.packages'] = ament
    modules['launch_ros'] = ModuleType('launch_ros')
    return modules


def _walk(item):
    """Yield every recorded object reachable from item."""
    if isinstance(item, Recorded):
        yield item
        yield from _walk(list(item.args))
        yield from _walk(list(item.kwargs.values()))
    elif isinstance(item, (list, tuple)):
        for child in item:
            yield from _walk(child)
    elif isinstance(item, dict):
        yield from _walk(list(item.values()))


def _description(**env):
    """Return the launch description built with the given environment."""
    with patch.dict(os.environ, {key: '' for key in ENV_KEYS}), patch.dict(os.environ, env):
        with patch.dict(sys.modules, _stub_modules()):
            spec = importlib.util.spec_from_file_location('devkit_launch_under_test', LAUNCH_FILE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.generate_launch_description()


def _nodes(description, **match):
    return [item for item in _walk(description)
            if type(item).__name__ == 'Node' and all(item.kwargs.get(k) == v for k, v in match.items())]


def _includes_bridge(description):
    return any(type(item).__name__ == 'PythonLaunchDescriptionSource'
               and str(item.args[0]).endswith('mavlink_bridge.launch.py') for item in _walk(description))


class TestMavlinkBridgeInclude(unittest.TestCase):
    def test_bridge_is_included_with_an_endpoint(self):
        """Verify MAVLINK_ENDPOINT includes mavlink_bridge.launch.py."""
        self.assertTrue(_includes_bridge(_description(MAVLINK_ENDPOINT='/dev/ttyACM2')))

    def test_bridge_is_not_included_without_an_endpoint(self):
        """Verify no endpoint means no bridge include."""
        self.assertFalse(_includes_bridge(_description()))

    def test_placeholder_endpoint_is_refused(self):
        """Verify the placeholder endpoint stops the launch description."""
        with self.assertRaises(RuntimeError):
            _description(MAVLINK_ENDPOINT='TODO_SERIAL_DEVICE_HERE')

    def test_driver_is_told_to_leave_cmd_vel_to_the_bridge(self):
        """Verify the endpoint is baked into the handle_cmd_vel expression for the driver."""
        with_bridge = _description(MAVLINK_ENDPOINT='/dev/ttyACM2')
        without = _description()
        flags = lambda d: [p for item in _walk(d) if type(item).__name__ == 'PythonExpression'
                           for p in item.args[0] if p in ('True', 'False')]
        self.assertIn('True', flags(with_bridge))
        self.assertNotIn('True', flags(without))


class TestFusionCorePath(unittest.TestCase):
    def test_fusioncore_and_relay_are_present_without_an_endpoint(self):
        """Verify FusionCore and the /fusion/odom to /fc/odom relay are launched with no endpoint."""
        description = _description()
        self.assertTrue(_nodes(description, package='fusioncore_ros'))
        self.assertTrue(_nodes(description, name='fusion_odom_to_fc_relay',
                               arguments=['/fusion/odom', '/fc/odom']))

    def test_fusioncore_gate_follows_the_endpoint(self):
        """Verify the FusionCore group's condition expression carries the endpoint state."""
        def gate(description):
            groups = [item for item in _walk(description) if type(item).__name__ == 'GroupAction'
                      and _nodes(item, package='fusioncore_ros')]
            self.assertEqual(len(groups), 1)
            return [p for e in _walk(groups[0].kwargs['condition']) if type(e).__name__ == 'PythonExpression'
                    for p in e.args[0]]
        self.assertEqual(gate(_description())[-1], 'False')
        self.assertEqual(gate(_description(MAVLINK_ENDPOINT='/dev/ttyACM2'))[-1], 'True')


class TestMapOdom(unittest.TestCase):
    def test_identity_static_transform_without_an_origin(self):
        """Verify no origin keeps the identity map -> odom and no anchor node."""
        description = _description()
        self.assertTrue(_nodes(description, name='map_to_odom_static'))
        self.assertFalse(_nodes(description, name='map_odom_anchor'))

    def test_anchor_replaces_the_static_transform_with_an_origin(self):
        """Verify an origin launches the anchor with the surveyed values and drops the identity transform."""
        description = _description(MAP_ORIGIN_LAT='51.5', MAP_ORIGIN_LON='-2.25', MAP_YAW_DEG='12',
                                   MAVLINK_ENDPOINT='/dev/ttyACM2')
        self.assertFalse(_nodes(description, name='map_to_odom_static'))
        (anchor,) = _nodes(description, name='map_odom_anchor')
        self.assertEqual(anchor.kwargs['parameters'], [{
            'origin_lat': 51.5, 'origin_lon': -2.25, 'map_yaw_deg': 12.0,
            'fix_topic': 'fc/ekf_fix', 'odom_topic': 'fc/odom'}])

    def test_anchor_reads_the_gnss_fix_without_the_bridge(self):
        """Verify the anchor listens to /gnss/fix when the bridge is not running."""
        description = _description(MAP_ORIGIN_LAT='51.5', MAP_ORIGIN_LON='-2.25')
        (anchor,) = _nodes(description, name='map_odom_anchor')
        self.assertEqual(anchor.kwargs['parameters'][0]['fix_topic'], 'gnss/fix')

    def test_invalid_origin_is_refused(self):
        """Verify a half-set, non-numeric or out-of-range origin stops the launch description."""
        for env in ({'MAP_ORIGIN_LAT': '51.5'}, {'MAP_ORIGIN_LAT': 'x', 'MAP_ORIGIN_LON': '1'},
                    {'MAP_ORIGIN_LAT': '95', 'MAP_ORIGIN_LON': '1'},
                    {'MAP_ORIGIN_LAT': '51', 'MAP_ORIGIN_LON': '1', 'MAP_YAW_DEG': 'nan'}):
            with self.subTest(env=env), self.assertRaises(RuntimeError):
                _description(**env)


if __name__ == '__main__':
    unittest.main()
