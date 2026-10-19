"""cmd_vel age check, soft e-stop acknowledgement and thread-safe scheduling."""
import asyncio
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).parent))

from support import FakeMessage, load  # pylint: disable=wrong-import-position

twist_handler = load('modules.twist_handler')
estop_handler = load('modules.estop_handler')
loop_module = load('loop')
robot_brain_handler = load('modules.robot_brain_handler')


def _run_now(coroutine):
    """Stand in for run_on_rosys_loop: run the coroutine to completion immediately."""
    asyncio.run(coroutine)


def _node(timeout=0.5):
    node = Mock()
    node.get_parameter.return_value.value = timeout
    return node


def _twist(linear=0.0, angular=0.0):
    message = twist_handler.Twist()
    message.linear.x = linear
    message.angular.z = angular
    return message


class TestTwistHandler(unittest.TestCase):
    def setUp(self):
        """Create a handler with a controllable clock and recording wheels."""
        self.now = 100.0
        self.wheels = Mock()
        self.wheels.drive = AsyncMock()
        self.wheels.stop = AsyncMock()
        self.node = _node()
        patcher = patch.object(twist_handler, 'run_on_rosys_loop', _run_now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.handler = twist_handler.TwistHandler(self.node, self.wheels, clock=lambda: self.now)

    def test_command_is_forwarded_to_the_wheels(self):
        """Verify a fresh command reaches the wheels unchanged."""
        self.handler.handle_twist(_twist(0.4, -0.2))
        self.wheels.drive.assert_awaited_once_with(0.4, -0.2)

    def test_moving_command_stops_once_after_the_timeout(self):
        """Verify a stale nonzero command stops the wheels exactly once."""
        self.handler.handle_twist(_twist(0.4))
        self.now += 0.5
        self.handler.check_stale()
        self.wheels.stop.assert_not_awaited()

        self.now += 0.01
        self.handler.check_stale()
        self.handler.check_stale()
        self.wheels.stop.assert_awaited_once_with()

    def test_refreshed_command_is_not_stopped(self):
        """Verify a stream of commands keeps the wheels running."""
        for _ in range(5):
            self.handler.handle_twist(_twist(0.4))
            self.now += 0.4
            self.handler.check_stale()
        self.wheels.stop.assert_not_awaited()

    def test_zero_command_needs_no_stop(self):
        """Verify an explicit zero command is not stopped again later."""
        self.handler.handle_twist(_twist(0.0, 0.0))
        self.now += 10.0
        self.handler.check_stale()
        self.wheels.stop.assert_not_awaited()

    def test_new_command_after_a_stop_moves_again_and_can_go_stale_again(self):
        """Verify the stale state resets when a new command arrives."""
        self.handler.handle_twist(_twist(0.4))
        self.now += 1.0
        self.handler.check_stale()
        self.handler.handle_twist(_twist(0.3))
        self.now += 1.0
        self.handler.check_stale()
        self.assertEqual(self.wheels.stop.await_count, 2)

    def test_nonfinite_command_is_ignored(self):
        """Verify NaN or infinity never reaches the wheels or refreshes the age."""
        self.handler.handle_twist(_twist(0.4))
        self.handler.handle_twist(_twist(float('nan')))
        self.handler.handle_twist(_twist(0.0, float('inf')))
        self.wheels.drive.assert_awaited_once_with(0.4, 0.0)
        self.now += 1.0
        self.handler.check_stale()
        self.wheels.stop.assert_awaited_once_with()

    def test_invalid_timeout_is_rejected(self):
        """Verify a nonpositive or nonfinite timeout parameter raises."""
        for value in (0.0, -1.0, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                twist_handler.TwistHandler(_node(value), self.wheels)

    def test_timer_and_subscription_are_registered(self):
        """Verify the age check runs on a timer and cmd_vel is subscribed."""
        self.node.create_timer.assert_called_once()
        self.assertEqual(self.node.create_subscription.call_args.args[1], 'cmd_vel')


class TestSoftEStopAcknowledgement(unittest.TestCase):
    def setUp(self):
        """Create a handler over a fake e-stop and recording publishers."""
        self.estop = Mock()
        self.estop.is_soft_estop_active = False
        self.estop.set_soft_estop = AsyncMock(side_effect=self._set)
        self.node = Mock()
        publishers = {}

        def create_publisher(_type, topic, _qos):
            publishers[topic] = Mock()
            return publishers[topic]

        self.node.create_publisher.side_effect = create_publisher
        for patcher in (patch.object(estop_handler, 'run_on_rosys_loop', _run_now),
                        patch.object(estop_handler.rosys, 'on_startup')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.handler = estop_handler.EStopHandler(self.node, self.estop)
        self.state = publishers['estop/soft/state']

    def _set(self, active):
        self.estop.is_soft_estop_active = active

    def _published(self):
        return [call.args[0].data for call in self.state.publish.call_args_list]

    def test_request_is_applied_then_acknowledged(self):
        """Verify the acknowledgement follows the applied state."""
        self.handler.soft_estop_callback(FakeMessage(data=True))
        self.handler.soft_estop_callback(FakeMessage(data=False))
        self.assertEqual(self._published(), [True, False])

    def test_failed_request_publishes_no_acknowledgement(self):
        """Verify a failing e-stop command never confirms a stop."""
        self.estop.set_soft_estop.side_effect = RuntimeError('lizard down')
        with self.assertRaises(RuntimeError):
            self.handler.soft_estop_callback(FakeMessage(data=True))
        self.state.publish.assert_not_called()

    def test_startup_publishes_the_current_state(self):
        """Verify a restarted driver reports its real soft e-stop state."""
        self.estop.is_soft_estop_active = True
        self.handler._check_on_startup()
        self.assertEqual(self._published(), [True])

    def test_requests_are_latched_on_the_subscription(self):
        """Verify the request subscription uses the latched safety profile."""
        args = self.node.create_subscription.call_args.args
        self.assertEqual(args[1], 'estop/soft')
        self.assertIs(args[3], estop_handler.SAFETY_QOS)


class TestRunOnRosysLoop(unittest.TestCase):
    def setUp(self):
        """Run a real event loop on a second thread, as RoSys does."""
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        patcher = patch.object(loop_module.core, 'loop', self.loop)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.loop.call_soon_threadsafe, self.loop.stop)

    def test_coroutine_runs_on_the_loop_thread(self):
        """Verify the coroutine executes on the loop's thread, not the caller's."""
        async def thread_id():
            return threading.get_ident()

        future = loop_module.run_on_rosys_loop(thread_id())
        self.assertEqual(future.result(2), self.thread.ident)
        self.assertNotEqual(self.thread.ident, threading.get_ident())

    def test_failure_is_logged(self):
        """Verify an exception in the coroutine is logged, not lost."""
        async def fail():
            raise RuntimeError('boom')

        with self.assertLogs('devkit_driver.loop', 'ERROR') as logs:
            future = loop_module.run_on_rosys_loop(fail())
            with self.assertRaises(RuntimeError):
                future.result(2)
            for _ in range(100):
                if logs.output:
                    break
                threading.Event().wait(0.02)
        self.assertIn('boom', logs.output[0])

    def test_stopped_loop_raises_and_closes_the_coroutine(self):
        """Verify a missing loop raises without leaking an unawaited coroutine."""
        async def never():
            return 1

        coroutine = never()
        with patch.object(loop_module.core, 'loop', None), self.assertRaises(RuntimeError):
            loop_module.run_on_rosys_loop(coroutine)
        self.assertIsNone(coroutine.cr_frame)


class TestRobotBrainHandler(unittest.TestCase):
    def test_commands_are_scheduled_on_the_loop(self):
        """Verify each robot-brain request is handed to the thread-safe scheduler."""
        brain = Mock()
        for name in ('enable_esp', 'disable_esp', 'reset_esp', 'restart', 'configure'):
            setattr(brain, name, AsyncMock())
        handler = robot_brain_handler.RobotBrainHandler(Mock(), brain)
        with patch.object(robot_brain_handler, 'run_on_rosys_loop', _run_now):
            for request in (handler._handle_enable, handler._handle_disable, handler._handle_reset,
                            handler._handle_restart, handler._handle_configure):
                request(None)
        for name in ('enable_esp', 'disable_esp', 'reset_esp', 'restart', 'configure'):
            getattr(brain, name).assert_awaited_once_with()


if __name__ == '__main__':
    unittest.main()
