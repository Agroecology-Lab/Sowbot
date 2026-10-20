"""Tests for SimWorldService with a fake worldgen runner and a temporary map file."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from devkit_ui.application_services.sim_world_service import SimWorldService, WorldRebuildError

ARGS = (0.5, 0.75, 1.0, 'maize', 'plant', 20)


class TestSimWorldService(unittest.TestCase):
    def setUp(self):
        """Create a saved map file, a crop model lookup and a fake worldgen runner."""
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.map_file = Path(tmp.name) / 'maize_map'
        self.map_file.write_text('nodes')
        self.assets = Mock()
        self.assets.has_crop_model.return_value = True
        self.run = Mock(return_value=SimpleNamespace(
            returncode=0, stdout='Weed density: 7\n', stderr=''))
        self.service = SimWorldService(
            self.assets, str(self.map_file), '/x/worldgen.sh', self.run)

    def test_rebuild_passes_every_argument_to_worldgen_and_returns_the_weed_count(self):
        """Verify the worldgen command line and that the reported weed count is returned."""
        self.assertEqual(self.service.rebuild(*ARGS), '7')
        self.assertEqual(self.run.call_args.args[0], [
            'bash', '/x/worldgen.sh', '--plant-spacing', '0.5', '--row-width', '0.75',
            '--plant-scale', '1.0', '--scale-category', 'maize', '--crop-model', 'plant',
            '--weed-density', '20'])

    def test_missing_weed_count_is_reported_as_unknown(self):
        """Verify worldgen output without a weed line yields '?'."""
        self.run.return_value.stdout = 'done'
        self.assertEqual(self.service.rebuild(*ARGS), '?')

    def test_missing_map_is_rejected_before_worldgen_runs(self):
        """Verify no process is started when no map has been saved."""
        self.map_file.unlink()
        with self.assertRaisesRegex(WorldRebuildError, 'no saved map'):
            self.service.rebuild(*ARGS)
        self.run.assert_not_called()

    def test_unknown_crop_model_is_rejected_before_worldgen_runs(self):
        """Verify no process is started for a crop model that is not on disk."""
        self.assets.has_crop_model.return_value = False
        with self.assertRaisesRegex(WorldRebuildError, 'model "plant" not found'):
            self.service.rebuild(*ARGS)
        self.run.assert_not_called()

    def test_failure_reports_the_tail_of_stderr(self):
        """Verify a non-zero exit becomes a user-facing error with the last 200 characters."""
        self.run.return_value = SimpleNamespace(returncode=2, stdout='', stderr='x' * 300 + 'boom')
        with self.assertRaisesRegex(WorldRebuildError, 'rebuild failed: x+boom$') as ctx:
            self.service.rebuild(*ARGS)
        self.assertEqual(len(str(ctx.exception)), len('rebuild failed: ') + 200)

    def test_timeout_becomes_a_rebuild_error(self):
        """Verify a worldgen timeout is reported as a rebuild error."""
        self.run.side_effect = subprocess.TimeoutExpired('bash', 120)
        with self.assertRaisesRegex(WorldRebuildError, 'timed out'):
            self.service.rebuild(*ARGS)

    def test_runner_is_bounded_captures_text_and_does_not_use_a_shell(self):
        self.service.rebuild(*ARGS)
        self.assertEqual(self.run.call_args.kwargs, {
            'capture_output': True, 'text': True, 'timeout': 120, 'check': False,
        })
        self.assets.has_crop_model.assert_called_once_with('plant')

    def test_zero_and_multidigit_counts_are_parsed_from_multiline_output(self):
        for output, expected in (
            ('generating\nWeed density: 0\ndone', '0'),
            ('Weed density:\t12345\n', '12345'),
            ('cached world', '?'),
            (None, '?'),
        ):
            with self.subTest(output=output):
                self.run.return_value.stdout = output
                self.assertEqual(self.service.rebuild(*ARGS), expected)

    def test_failure_prefers_stderr_then_stdout_then_unknown_error(self):
        for stderr, stdout, expected in (
            ('  stderr failure\n', 'stdout failure', 'stderr failure'),
            ('', '  stdout failure\n', 'stdout failure'),
            (None, None, 'unknown error'),
        ):
            with self.subTest(stderr=stderr, stdout=stdout):
                self.run.return_value = SimpleNamespace(
                    returncode=1, stderr=stderr, stdout=stdout)
                with self.assertRaises(WorldRebuildError) as caught:
                    self.service.rebuild(*ARGS)
                self.assertEqual(str(caught.exception), f'rebuild failed: {expected}')

    def test_timeout_preserves_cause_and_does_not_retry(self):
        timeout = subprocess.TimeoutExpired('bash', 120)
        self.run.side_effect = timeout
        with self.assertRaises(WorldRebuildError) as caught:
            self.service.rebuild(*ARGS)
        self.assertIs(caught.exception.__cause__, timeout)
        self.run.assert_called_once()

    def test_process_start_error_propagates_without_becoming_rebuild_error(self):
        error = PermissionError('cannot execute worldgen')
        self.run.side_effect = error
        with self.assertRaises(PermissionError) as caught:
            self.service.rebuild(*ARGS)
        self.assertIs(caught.exception, error)

    def test_arguments_with_spaces_and_metacharacters_remain_single_values(self):
        self.service.rebuild(0.1, 0.2, 0.05, 'crop', 'maize; echo unexpected', 0)
        self.assertEqual(self.run.call_args.args[0], [
            'bash', '/x/worldgen.sh', '--plant-spacing', '0.1', '--row-width', '0.2',
            '--plant-scale', '0.05', '--scale-category', 'crop',
            '--crop-model', 'maize; echo unexpected', '--weed-density', '0',
        ])


if __name__ == '__main__':
    unittest.main()
