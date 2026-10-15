"""Verify Python logging from the UI services reaches the ROS logger."""
import logging
import sys
import types
import unittest
from unittest import mock

with mock.patch.dict(sys.modules, {
    'rclpy': types.ModuleType('rclpy'),
    'rclpy.node': types.SimpleNamespace(Node=type('Node', (), {})),
}):
    from devkit_ui import ros_gateway

LOGGER_NAME = 'devkit_ui_log_forwarding_test'


class TestRosLogForwarding(unittest.TestCase):
    def setUp(self) -> None:
        """Forward a private logger tree to a node whose ROS logger is a mock."""
        self.node = mock.Mock()
        self.ros_logger = self.node.get_logger.return_value
        self.gateway = ros_gateway.RosGateway(self.node)
        self.gateway.forward_python_logging(LOGGER_NAME)
        self.python_logger = logging.getLogger(LOGGER_NAME)
        self.addCleanup(self.python_logger.handlers.clear)
        self.child = logging.getLogger(f'{LOGGER_NAME}.child')

    def test_each_level_uses_matching_ros_severity(self) -> None:
        """Verify info, warning and error records land on the matching ROS severity."""
        self.child.info('an info')
        self.child.warning('a warning')
        self.child.error('an error')

        self.ros_logger.info.assert_called_once_with('an info')
        self.ros_logger.warn.assert_called_once_with('a warning')
        self.ros_logger.error.assert_called_once_with('an error')

    def test_logged_exception_includes_traceback(self) -> None:
        """Verify logger.exception keeps the traceback in the ROS message."""
        try:
            raise ValueError('boom')
        except ValueError:
            self.child.exception('plan failed')

        message = self.ros_logger.error.call_args.args[0]
        self.assertIn('plan failed', message)
        self.assertIn('ValueError: boom', message)

    def test_forwarding_twice_does_not_double_lines(self) -> None:
        """Verify a second call replaces the first handler instead of adding another."""
        self.gateway.forward_python_logging(LOGGER_NAME)

        self.child.warning('once')

        self.ros_logger.warn.assert_called_once_with('once')

    def test_info_is_not_dropped(self) -> None:
        """Verify info records pass even when the root logger is at its default WARNING."""
        logging.getLogger().setLevel(logging.WARNING)

        self.child.info('kept')

        self.ros_logger.info.assert_called_once_with('kept')


if __name__ == '__main__':
    unittest.main()
