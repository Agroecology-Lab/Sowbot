"""Heading validity must distinguish fresh observations from cached publications."""

# pylint: disable=protected-access

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).parents[1]


@pytest.fixture(name='heading_sources')
def _heading_sources(monkeypatch):
    """Load both real nodes with passive ROS doubles and a controllable clock."""
    class Node:
        def __init__(self, _name):
            self.now_s = 0.0
            self.publishers = {}

        def create_publisher(self, _message_type, topic, _qos):
            self.publishers[topic] = Mock()
            return self.publishers[topic]

        create_subscription = Mock()
        create_timer = Mock()
        get_logger = Mock()
        declare_parameter = Mock()
        get_parameter = Mock(return_value=types.SimpleNamespace(value=False))

        def get_clock(self):
            now = types.SimpleNamespace(nanoseconds=int(self.now_s * 1e9), to_msg=lambda: self.now_s)
            return types.SimpleNamespace(now=lambda: now)

    def imu():
        return types.SimpleNamespace(
            header=types.SimpleNamespace(stamp=None, frame_id=''),
            orientation=types.SimpleNamespace(x=0.0, y=0.0, z=0.0, w=0.0),
            orientation_covariance=[0.0] * 9,
            angular_velocity_covariance=[0.0] * 9,
            linear_acceleration_covariance=[0.0] * 9,
        )

    modules = {name: types.ModuleType(name) for name in (
        'rclpy', 'rclpy.node', 'sensor_msgs', 'sensor_msgs.msg',
        'std_msgs', 'std_msgs.msg', 'ublox_ubx_msgs', 'ublox_ubx_msgs.msg',
    )}
    modules['rclpy.node'].Node = Node
    modules['sensor_msgs.msg'].Imu = imu
    modules['std_msgs.msg'].Bool = types.SimpleNamespace
    modules['ublox_ubx_msgs.msg'].UBXNavPVT = types.SimpleNamespace
    modules['ublox_ubx_msgs.msg'].UBXNavRelPosNED = types.SimpleNamespace
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    def load(name, class_name):
        path = ROOT / 'src/devkit_driver/devkit_driver' / f'{name}.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, class_name)()

    return types.SimpleNamespace(
        cog=load('course_over_ground', 'CourseOverGround'),
        dual=load('relposned_heading_shim', 'RelPosnedHeadingShim'),
    )


def _pvt(**overrides):
    values = {'gnss_fix_ok': True, 'invalid_llh': False, 'g_speed': 1000,
              'head_acc': 1000, 'head_mot': 0}
    return types.SimpleNamespace(**(values | overrides))


def _relposned(**overrides):
    values = {'rel_pos_valid': True, 'rel_pos_heading_valid': True,
              'rel_pos_length': 100, 'rel_pos_heading': 0, 'acc_heading': 1000}
    return types.SimpleNamespace(**(values | overrides))


def _valid(node):
    return node.publishers['/gnss/heading_valid'].publish.call_args.args[0].data


def _invalidity_signal(node):
    config = yaml.safe_load((ROOT / 'src/devkit_bringup/config/sowbot_monitor.yaml').read_text())
    monitor = next(item for item in config['monitors'] if item['name'] == '/gnss/heading_valid')
    assert monitor['message_type'] == 'std_msgs/msg/Bool'
    assert monitor['signal_when']['autonomy_critical']
    assert monitor['signal_when']['timeout'] == 2.0
    signal = monitor['signal_lambdas'][0]
    assert signal['autonomy_critical'] and not signal['safety_critical']
    predicate = eval(signal['expression'], {'__builtins__': {}})  # pylint: disable=eval-used
    return predicate(types.SimpleNamespace(data=_valid(node)))


def test_cached_heading_trips_monitor_even_with_low_variance(heading_sources):
    node = heading_sources.cog
    node._cb(_pvt())
    assert _valid(node) is True
    assert not _invalidity_signal(node)

    node.now_s = 0.1
    node._republish_stale()
    assert _valid(node) is True
    assert node._pub.publish.call_count == 1

    node.now_s = 1.0
    node._republish_stale()
    cached = node._pub.publish.call_args.args[0]
    assert cached.orientation_covariance[8] < 0.0025
    assert node._pub.publish.call_count == 2
    assert _invalidity_signal(node)

    node._cb(_pvt())
    assert _valid(node) is True
    assert not _invalidity_signal(node)


@pytest.mark.parametrize('age, heading_count', [(10.0, 2), (10.001, 1)])
def test_cache_timeout_keeps_reporting_invalid(heading_sources, age, heading_count):
    node = heading_sources.cog
    node._cb(_pvt())
    node.now_s = age
    node._republish_stale()
    assert node._pub.publish.call_count == heading_count
    assert _valid(node) is False


def test_no_accepted_heading_reports_invalid(heading_sources):
    node = heading_sources.cog
    node._republish_stale()
    assert _valid(node) is False
    node._pub.publish.assert_not_called()


@pytest.mark.parametrize('rejected', [
    {'gnss_fix_ok': False}, {'invalid_llh': True}, {'g_speed': 499}, {'head_acc': 3000001},
])
def test_rejected_cog_input_invalidates_heading(heading_sources, rejected):
    node = heading_sources.cog
    node._cb(_pvt())
    node._cb(_pvt(**rejected))
    assert _valid(node) is False
    assert node._pub.publish.call_count == 1


@pytest.mark.parametrize('rejected', [
    {'rel_pos_valid': False}, {'rel_pos_heading_valid': False}, {'rel_pos_length': 29},
])
def test_dual_antenna_gates_and_recovery(heading_sources, rejected):
    node = heading_sources.dual
    node._cb(_relposned(**rejected))
    assert _valid(node) is False
    node._pub.publish.assert_not_called()
    node._cb(_relposned())
    assert _valid(node) is True
    assert not _invalidity_signal(node)


def test_disabled_cog_does_not_publish_heading_or_validity(heading_sources):
    node = heading_sources.cog
    node._dual_antenna = True
    node._cb(_pvt())
    node._republish_stale()
    for publisher in node.publishers.values():
        publisher.publish.assert_not_called()
