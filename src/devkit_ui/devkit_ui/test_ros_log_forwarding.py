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
        self.python_logger = logging.getLogger(LOGGER_NAME)
        self.child = logging.getLogger(f'{LOGGER_NAME}.child')
        for logger in (self.python_logger, self.child):
            self.enterContext(mock.patch.object(logger, 'handlers', []))
            self.enterContext(mock.patch.object(logger, 'filters', []))
            self.enterContext(mock.patch.object(logger, 'propagate', logger is self.child))
            self.addCleanup(logger.setLevel, logger.level)
        self.python_logger.setLevel(logging.WARNING)
        self.child.setLevel(logging.NOTSET)
        self.node = mock.Mock()
        self.ros_logger = self.node.get_logger.return_value
        self.gateway = ros_gateway.RosGateway(self.node)
        self.gateway.forward_python_logging(LOGGER_NAME)

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
        self.addCleanup(logging.getLogger().setLevel, logging.getLogger().level)
        logging.getLogger().setLevel(logging.WARNING)

        self.child.info('kept')

        self.ros_logger.info.assert_called_once_with('kept')

    def test_explicit_child_debug_and_critical_use_debug_and_error(self) -> None:
        """Forward enabled child debug records and map critical records to ROS error."""
        self.child.setLevel(logging.DEBUG)

        self.child.debug('row %s', 7)
        self.child.critical('failed %s', 'save')

        self.assertEqual(self.ros_logger.method_calls, [
            mock.call.debug('row 7'), mock.call.error('failed save')])

    def test_child_level_filters_and_propagation_control_forwarding(self) -> None:
        """Respect the emitting logger's configuration before forwarding to ROS."""
        self.child.setLevel(logging.ERROR)
        self.child.warning('below child level')
        self.child.setLevel(logging.INFO)
        self.child.addFilter(lambda _record: False)
        self.child.info('filtered out')
        self.child.filters.clear()
        self.child.propagate = False
        self.child.info('local only')

        self.assertEqual(self.ros_logger.method_calls, [])

    def test_other_handlers_and_propagation_are_preserved(self) -> None:
        """Keep existing handlers and the logger's propagation setting on reconfiguration."""
        other = mock.Mock(spec=logging.Handler, level=logging.NOTSET)
        self.python_logger.addHandler(other)

        self.gateway.forward_python_logging(LOGGER_NAME)
        self.child.info('both destinations')

        self.assertIn(other, self.python_logger.handlers)
        self.assertFalse(self.python_logger.propagate)
        other.handle.assert_called_once()
        self.ros_logger.info.assert_called_once_with('both destinations')

    def test_reconfiguration_sends_records_to_the_new_node_only(self) -> None:
        """Replacing the gateway must release the previous ROS log destination."""
        replacement = mock.Mock()

        ros_gateway.RosGateway(replacement).forward_python_logging(LOGGER_NAME)
        self.child.warning('new node')

        self.assertEqual(self.ros_logger.method_calls, [])
        replacement.get_logger.return_value.warn.assert_called_once_with('new node')

    def test_formatting_and_ros_failures_use_handler_error_handling(self) -> None:
        """Report forwarding failures through logging without leaking them to callers."""
        handler = self.python_logger.handlers[0]
        record = logging.LogRecord(
            LOGGER_NAME, logging.INFO, __file__, 1, 'plan %s', ('ok',), None)
        targets = ((handler, 'format'), (self.node, 'get_logger'), (self.ros_logger, 'info'))
        for target, method in targets:
            with (
                self.subTest(method=method),
                mock.patch.object(target, method, side_effect=RuntimeError('unavailable')),
                mock.patch.object(handler, 'handleError') as handle_error,
            ):
                handler.handle(record)

                handle_error.assert_called_once_with(record)


if __name__ == '__main__':
    unittest.main()
