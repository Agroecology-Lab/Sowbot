"""Regression tests for mission resolution, execution cleanup, and tool failures."""
# pylint: disable=protected-access
import unittest
from unittest.mock import Mock, call, patch

from devkit_ui.test_mission_domain_service import doc, mds, node


class TestResolutionBoundaries(unittest.TestCase):
    def test_missing_rows_are_sorted_unique_and_include_both_missing_roles(self):
        """Report every incomplete queued row once, including a missing entry."""
        topo = doc(node('exit', 3, 'exit'), node('entry', 1, 'entry'))
        queue = [(3, 'drive', None), (1, 'weed', None), (3, 'weed', None), (2, 'drive', None)]
        with self.assertRaisesRegex(mds.MissionError, r'row\(s\) \[1, 2, 3\]$'):
            mds.resolve_steps(topo, queue)

    def test_invalid_metadata_does_not_hide_valid_row(self):
        """Ignore absent, nonconvertible, and unrelated metadata during resolution."""
        topo = doc(node('bad', [], 'entry'), node('unassigned', None, 'exit'),
                   node('middle', 4, 'middle'), node('E', '4', 'entry'), node('X', 4, 'exit'))
        self.assertEqual(mds.resolve_steps(topo, [(4, 'drive', None)]),
                         [(4, 'E', 'X', 'drive', None)])

    def test_repeated_rows_keep_queue_order_and_independent_parameters(self):
        """Keep repeated visits and their actions even when the map order differs."""
        topo = doc(node('X', 4, 'exit'), node('E', 4, 'entry'))
        params = {'depth': 0.4}
        queue = [(4, 'weed', params), (4, 'drive', None)]
        self.assertEqual(mds.resolve_steps(topo, queue),
                         [(4, 'E', 'X', 'weed', {'depth': 0.4}), (4, 'E', 'X', 'drive', None)])
        self.assertEqual(queue, [(4, 'weed', {'depth': 0.4}), (4, 'drive', None)])
        self.assertEqual(params, {'depth': 0.4})

    def test_empty_queue_on_loaded_map_has_no_steps(self):
        """An empty queue resolves without inventing a mission step."""
        self.assertEqual(mds.resolve_steps(doc(), []), [])


class TestExecutionBoundaries(unittest.TestCase):
    def setUp(self):
        """Prepare a two-row mission with mocked tool and status callbacks."""
        self.ros = Mock()
        self.service = mds.MissionDomainService(self.ros)
        self.service.publish_tool_msgs = Mock()
        self.status = Mock()
        self.steps = [(7, 'E7', 'X7', 'weed', {'depth': 0.4}),
                      (2, 'E2', 'X2', 'custom', None)]

    def execute(self, navigate, estop=lambda: False):
        """Run the worker deterministically, checking cleanup on every exit path."""
        self.service._running = True
        self.service._execute(self.steps, navigate, self.status, estop)
        self.assertFalse(self.service.running)
        self.assertFalse(self.service.cancel_requested)

    def test_multiple_rows_preserve_action_parameters_and_progress(self):
        """Verify navigation and tool command order, parameters, and progress for both rows."""
        order = Mock()
        navigate = Mock(return_value=True)
        order.attach_mock(navigate, 'navigate')
        order.attach_mock(self.service.publish_tool_msgs, 'publish')
        self.execute(navigate)
        self.assertEqual(order.mock_calls, [
            call.navigate('E7'), call.publish('weed', {'depth': 0.4}, enable=True),
            call.navigate('X7'), call.publish('weed', {'depth': 0.4}, enable=False),
            call.navigate('E2'), call.publish('custom', None, enable=True),
            call.navigate('X2'), call.publish('custom', None, enable=False),
        ])
        progress = [c.args[1] for c in self.status.call_args_list if c.args[0] == mds.PROGRESS]
        self.assertEqual(len(progress), 4)
        self.assertTrue(progress[0].startswith('[1/2] Row 7'))
        self.assertEqual(progress[-1], '[2/2] Row 2 custom → X2')
        self.assertEqual(self.status.call_args, call(mds.OK, 'Mission complete — 2 step(s) done ✓'))

    def test_cancel_before_first_goal_prevents_navigation_and_publishing(self):
        """Verify a pending cancellation stops execution before any navigation or tool command."""
        self.service._running = True  # pylint: disable=protected-access  # cancel() ignores an idle service
        self.service.cancel()
        navigate = Mock(return_value=True)
        self.execute(navigate)
        navigate.assert_not_called()
        self.service.publish_tool_msgs.assert_not_called()
        self.status.assert_called_once_with(mds.WARN, 'Cancelled')

    def test_estop_after_transit_prevents_tool_activation(self):
        """Verify an e-stop after entry navigation prevents traversal and tool activation."""
        navigate = Mock(return_value=True)
        self.execute(navigate, Mock(side_effect=[False, True]))
        navigate.assert_called_once_with('E7')
        self.service.publish_tool_msgs.assert_not_called()
        self.assertEqual(self.status.call_args, call(mds.WARN, 'Cancelled'))

    def test_cancel_during_traversal_disengages_tool_and_prevents_next_row(self):
        """Verify cancellation during traversal disables the tool and skips subsequent rows."""
        def navigate(target):
            """Simulate successful navigation, requesting cancellation at the first row's exit."""
            if target == 'X7':
                self.service.cancel()
            return True

        navigation = Mock(side_effect=navigate)
        self.execute(navigation)
        self.assertEqual(navigation.call_args_list, [call('E7'), call('X7')])
        self.assertEqual(self.service.publish_tool_msgs.call_args_list, [
            call('weed', {'depth': 0.4}, enable=True),
            call('weed', {'depth': 0.4}, enable=False),
        ])
        self.assertEqual(self.status.call_args, call(mds.WARN, 'Cancelled'))

    def test_failed_or_exceptional_navigation_prevents_later_rows(self):
        """Verify failed entry or exit navigation stops the mission and cleans up active tools."""
        for target in ('E7', 'X7'):
            for raises in (False, True):
                with self.subTest(target=target, raises=raises):
                    self.status.reset_mock()
                    self.service.publish_tool_msgs.reset_mock()

                    def navigate(destination, failed_target=target, should_raise=raises):
                        """Return false or raise an error at the selected destination."""
                        if destination == failed_target:
                            if should_raise:
                                raise RuntimeError('navigation disconnected')
                            return False
                        return True

                    navigation = Mock(side_effect=navigate)
                    self.execute(navigation)
                    expected = [call('E7')] if target == 'E7' else [call('E7'), call('X7')]
                    self.assertEqual(navigation.call_args_list, expected)
                    expected_tools = [] if target == 'E7' else [
                        call('weed', {'depth': 0.4}, enable=True),
                        call('weed', {'depth': 0.4}, enable=False),
                    ]
                    self.assertEqual(self.service.publish_tool_msgs.call_args_list, expected_tools)
                    self.assertEqual(self.status.call_args.args[0], mds.ERROR)
                    if raises:
                        self.assertIn('navigation disconnected', self.status.call_args.args[1])
                    self.assertNotIn(mds.OK, [c.args[0] for c in self.status.call_args_list])

    def test_status_callback_failure_still_releases_mission_state(self):
        """Verify status callback errors propagate after clearing running and cancellation flags."""
        self.status.side_effect = RuntimeError('UI disconnected')
        self.service._running = True
        navigate = Mock()
        with self.assertRaisesRegex(RuntimeError, 'UI disconnected'):
            self.service._execute(self.steps, navigate, self.status, lambda: False)
        self.assertFalse(self.service.running)
        self.assertFalse(self.service.cancel_requested)
        navigate.assert_not_called()

    def test_start_resets_stale_cancel_and_passes_callbacks_to_daemon_worker(self):
        """Verify starting clears stale cancellation and configures the worker's callbacks."""
        self.service.cancel()
        navigate, estop = Mock(), Mock(return_value=False)
        with patch.object(mds.threading, 'Thread') as worker:
            self.service.start(self.steps, navigate, self.status, estop)
            self.assertTrue(self.service.running)
            self.assertFalse(self.service.cancel_requested)
            worker.assert_called_once_with(
                target=self.service._execute,
                args=(self.steps, navigate, self.status, estop), daemon=True)
            worker.return_value.start.assert_called_once_with()
            self.status.assert_called_once_with(mds.INFO, 'Starting 2 step(s)…')
            navigate.assert_not_called()
            worker.call_args.kwargs['target'](*worker.call_args.kwargs['args'])
        self.assertFalse(self.service.running)


class TestToolPublicationBoundaries(unittest.TestCase):
    def setUp(self):
        """Create a mission service whose gateway supplies distinct mock publishers."""
        self.ros = Mock()
        self.ros.create_publisher.side_effect = lambda *_args: Mock()
        self.service = mds.MissionDomainService(self.ros)

    def test_cache_separates_topics_and_types_and_reuses_each_publisher(self):
        """Verify publisher caching distinguishes both topic names and message types."""
        publishers = [self.service.get_tool_publisher(topic, is_float)
                      for topic, is_float in [('/a', False), ('/a', True), ('/b', False)]]
        for key, publisher in zip(
                [('/a', False), ('/a', True), ('/b', False)], publishers, strict=True):
            self.assertIs(self.service.get_tool_publisher(*key), publisher)
        self.assertEqual(len({id(p) for p in publishers}), 3)
        self.assertEqual(self.ros.create_publisher.call_args_list, [
            call(mds.Bool, '/a', 1), call(mds.Float64, '/a', 1), call(mds.Bool, '/b', 1),
        ])

    def test_bool_and_numeric_values_use_fresh_correctly_typed_messages(self):
        """Verify tool values produce independent Bool or Float64 messages with converted data."""
        values = [(True, mds.Bool, True), (False, mds.Bool, False),
                  (0, mds.Float64, 0.0), (-2, mds.Float64, -2.0),
                  (1.25, mds.Float64, 1.25), ('3.5', mds.Float64, 3.5)]
        messages = []
        for value, message_type, expected in values:
            with self.subTest(value=value):
                with patch.object(
                        mds, 'action_ros_msgs', return_value=[('/tool', value)]) as mapper:
                    self.service.publish_tool_msgs('custom', {'depth': 2}, False)
                mapper.assert_called_once_with('custom', {'depth': 2}, False)
                publisher = self.service.get_tool_publisher('/tool', message_type is mds.Float64)
                message = publisher.publish.call_args.args[0]
                self.assertIsInstance(message, message_type)
                self.assertEqual(message.data, expected)
                self.assertIs(type(message.data), type(expected))
                messages.append(message)
        self.assertEqual(len({id(m) for m in messages}), len(values))
        self.assertEqual([m.data for m in messages], [True, False, 0.0, -2.0, 1.25, 3.5])

    def test_invalid_numeric_string_is_logged_and_later_topic_is_published(self):
        """Verify invalid numeric data is logged while publication continues for later topics."""
        with patch.object(mds, 'action_ros_msgs', return_value=[('/bad', 'oops'), ('/good', 4)]):
            self.service.publish_tool_msgs('custom', None, True)
        self.service.get_tool_publisher('/bad', True).publish.assert_not_called()
        good = self.service.get_tool_publisher('/good', True)
        self.assertEqual(good.publish.call_args.args[0].data, 4.0)
        self.ros.get_logger.return_value.warn.assert_called_once()
        self.assertIn('/bad, enable=True', self.ros.get_logger.return_value.warn.call_args.args[0])

    def test_publish_failure_does_not_prevent_remaining_tool_messages(self):
        """Verify a publisher error is logged without suppressing subsequent tool commands."""
        broken = self.service.get_tool_publisher('/broken')
        broken.publish.side_effect = RuntimeError('publisher disconnected')
        with patch.object(
                mds, 'action_ros_msgs', return_value=[('/broken', False), ('/good', False)]):
            self.service.publish_tool_msgs('custom', None, False)
        good = self.service.get_tool_publisher('/good')
        self.assertIs(good.publish.call_args.args[0].data, False)
        self.ros.get_logger.return_value.warn.assert_called_once()
        warning = self.ros.get_logger.return_value.warn.call_args.args[0]
        self.assertIn('publisher disconnected', warning)

    def test_publisher_creation_failure_is_retried_and_does_not_poison_cache(self):
        """Verify publisher creation is retried after failure and cached after success."""
        publisher = Mock()
        self.ros.create_publisher.side_effect = [RuntimeError('unavailable'), publisher]
        self.service.publish_tool_msgs('weed', None, True)
        self.service.publish_tool_msgs('weed', None, False)
        self.assertEqual(self.ros.create_publisher.call_count, 2)
        publisher.publish.assert_called_once()
        self.assertIs(publisher.publish.call_args.args[0].data, False)
        self.assertIs(self.service.get_tool_publisher('/tool/weeder/enable'), publisher)
        self.ros.get_logger.return_value.warn.assert_called_once()

    def test_actions_without_tool_topics_do_not_create_publishers(self):
        """Verify drive and unknown actions publish nothing for either enable state."""
        for action in ('drive', 'unknown'):
            for enable in (True, False):
                self.service.publish_tool_msgs(action, None, enable)
        self.ros.create_publisher.assert_not_called()
        self.ros.get_logger.assert_not_called()
