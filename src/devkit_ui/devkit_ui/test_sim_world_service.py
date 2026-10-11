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


if __name__ == '__main__':
    unittest.main()
