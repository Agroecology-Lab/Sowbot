"""Hardware mode must not start on placeholder safety values unless that is chosen explicitly."""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HARDWARE = 'GPS_PORT_ROVER=/dev/ttyACM0\nMCU_PORT=/dev/ttyACM1\nSOWBOT_UI_PASSWORD=secret\n'
CONFIG = Path('src/devkit_bringup/config')


class _Stop(Exception):
    """Raised to halt run() once the checks have been logged."""


def _run_output(env_text, files=None):
    """Capture startup checks using temporary configuration and stop before Docker runs."""
    spec = importlib.util.spec_from_file_location('manage', Path(__file__).parents[1] / 'manage.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory, patch.object(module.signal, 'signal'):
        manager = module.DevkitManager()
        manager.root_dir = Path(directory)
        (manager.root_dir / '.env').write_text(env_text)
        for name, text in (files or {}).items():
            path = manager.root_dir / CONFIG / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                patch.object(module.subprocess, 'run', side_effect=_Stop), \
                patch.object(module.os, 'system'):
            with contextlib.suppress(_Stop):
                manager.run([])
        return out.getvalue()


def _lever_arms(x, y, z):
    """Build FusionCore YAML containing the supplied antenna lever-arm coordinates."""
    return {'fusioncore.yaml': f'fusioncore:\n  ros__parameters:\n    gnss.lever_arm_x: {x}\n'
                               f'    gnss.lever_arm_y: {y}\n    gnss.lever_arm_z: {z}\n'}


class TestLeverArmGuard(unittest.TestCase):
    def setUp(self):
        """Keep a caller's FORCE_SIM setting from altering the test scenarios."""
        environment = patch.dict(os.environ, {'FORCE_SIM': ''})
        environment.start()
        self.addCleanup(environment.stop)

    def test_all_zero_lever_arms_are_refused(self):
        """Verify hardware startup rejects an unmeasured all-zero antenna offset."""
        with self.assertRaises(SystemExit) as raised:
            _run_output(HARDWARE, _lever_arms('0.0', '0.0', '0.0'))
        self.assertEqual(raised.exception.code, 1)

    def test_a_measured_lever_arm_starts(self):
        """Verify measured antenna offsets allow hardware startup without a lever-arm warning."""
        out = _run_output(HARDWARE, _lever_arms('0.45', '0.0', '1.2'))
        self.assertIn('Sim: FALSE', out)
        self.assertNotIn('lever arms', out)

    def test_a_single_zero_axis_is_still_a_measurement(self):
        """Verify a zero coordinate is accepted when another coordinate is nonzero."""
        self.assertIn('Sim: FALSE', _run_output(HARDWARE, _lever_arms('0.0', '0.0', '1.2')))

    def test_bench_override_starts_with_a_warning(self):
        """Verify the explicit bench override permits zero offsets and emits a warning."""
        out = _run_output(HARDWARE + 'ALLOW_UNMEASURED_LEVER_ARM=1\n', _lever_arms('0', '0', '0'))
        self.assertIn('Sim: FALSE', out)
        self.assertIn('zero antenna lever arms', out)

    def test_commented_out_values_are_not_read(self):
        """Verify commented offsets produce an unchecked warning without blocking startup."""
        text = {'fusioncore.yaml': '    # gnss.lever_arm_x: 0.0\n    # gnss.lever_arm_y: 0.0\n'
                                   '    # gnss.lever_arm_z: 0.0\n'}
        out = _run_output(HARDWARE, text)
        self.assertIn('lever arms not checked', out)
        self.assertIn('Sim: FALSE', out)

    def test_missing_config_warns_instead_of_blocking(self):
        """Verify missing FusionCore configuration warns while allowing hardware startup."""
        out = _run_output(HARDWARE)
        self.assertIn('lever arms not checked', out)
        self.assertIn('Sim: FALSE', out)

    def test_unparsable_value_warns_instead_of_blocking(self):
        """Verify malformed offset values are reported as unchecked rather than rejected."""
        out = _run_output(HARDWARE, _lever_arms('0.0', 'abc', '0.0'))
        self.assertIn('lever arms not checked', out)

    def test_simulation_is_not_checked(self):
        """Verify simulation skips the hardware antenna-offset guard."""
        out = _run_output('GPS_PORT_ROVER=virtual\nMCU_PORT=virtual\n', _lever_arms('0', '0', '0'))
        self.assertIn('Sim: TRUE', out)
        self.assertNotIn('lever', out)


class TestUnconfirmedSettings(unittest.TestCase):
    def setUp(self):
        """Keep a caller's FORCE_SIM setting from altering the test scenarios."""
        environment = patch.dict(os.environ, {'FORCE_SIM': ''})
        environment.start()
        self.addCleanup(environment.stop)

    def test_unconfirmed_monitor_settings_are_reported(self):
        """Verify hardware startup reports monitor settings marked for confirmation."""
        files = {**_lever_arms('0.5', '0', '1'),
                 'sowbot_monitor.yaml': '  - expression: "lambda x: x.voltage < 44.0"   # TODO: CONFIRM BMS cutoff\n'}
        out = _run_output(HARDWARE, files)
        self.assertIn('Unconfirmed setting', out)
        self.assertIn('x.voltage < 44.0', out)
        self.assertIn('Sim: FALSE', out)

    def test_nothing_reported_when_everything_is_confirmed(self):
        """Verify confirmed monitor configuration produces no unconfirmed-setting warning."""
        files = {**_lever_arms('0.5', '0', '1'), 'sowbot_monitor.yaml': 'topics: []\n'}
        self.assertNotIn('Unconfirmed', _run_output(HARDWARE, files))
