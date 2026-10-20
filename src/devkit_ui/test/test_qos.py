"""Verify shared QoS descriptions stay immutable and independent of ROS imports."""
import importlib
import sys
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import devkit_ui

qos = importlib.import_module('devkit_ui.qos')


class TestQosSpec(unittest.TestCase):
    def test_optional_settings_default_to_none(self):
        """A minimal spec must let rclpy choose all optional transport settings."""
        spec = qos.QosSpec(depth=3, reliability='best_effort')
        self.assertEqual((spec.depth, spec.reliability), (3, 'best_effort'))
        self.assertEqual(
            (spec.durability, spec.history, spec.liveliness, spec.liveliness_lease_sec),
            (None, None, None, None))

    def test_shared_specs_cannot_be_changed_by_a_consumer(self):
        """One service must not change transport settings used by other services."""
        for name in ('SAFETY_QOS', 'SENSOR_QOS', 'ODOM_QOS', 'LATCHED_QOS'):
            with self.subTest(spec=name):
                spec = getattr(qos, name)
                original_depth = spec.depth
                with self.assertRaises(FrozenInstanceError):
                    spec.depth = 42
                self.assertEqual(spec.depth, original_depth)

    def test_module_imports_without_rclpy_or_rclpy_qos(self):
        """Load a fresh copy so a cached module cannot hide a restored ROS dependency."""
        with patch.object(devkit_ui, 'qos', create=True), patch.dict(
                sys.modules, {'rclpy': None, 'rclpy.qos': None}):
            sys.modules.pop('devkit_ui.qos', None)
            imported = importlib.import_module('devkit_ui.qos')
            for name in ('SAFETY_QOS', 'SENSOR_QOS', 'ODOM_QOS', 'LATCHED_QOS'):
                with self.subTest(spec=name):
                    self.assertIsInstance(getattr(imported, name), imported.QosSpec)


if __name__ == '__main__':
    unittest.main()
