"""limit_velocity clamps both components symmetrically and rejects bad limits."""

import unittest

from devkit_mavlink_bridge.modules.velocity_limits import limit_velocity


class LimitVelocityTest(unittest.TestCase):
    """Clamping of linear and angular velocity."""

    def test_inside_the_limits_is_unchanged(self):
        """Verify commands within the limits pass through."""
        self.assertEqual(limit_velocity(0.5, -0.3, 1.6, 1.0), (0.5, -0.3))

    def test_values_are_clamped_in_both_directions(self):
        """Verify forward, reverse and both turn directions are limited."""
        self.assertEqual(limit_velocity(5.0, 5.0, 1.6, 1.0), (1.6, 1.0))
        self.assertEqual(limit_velocity(-5.0, -5.0, 1.6, 1.0), (-1.6, -1.0))

    def test_exactly_at_the_limit_is_kept(self):
        """Verify the limit itself is allowed."""
        self.assertEqual(limit_velocity(1.6, -1.0, 1.6, 1.0), (1.6, -1.0))

    def test_nonfinite_input_becomes_zero(self):
        """Verify NaN and infinity never pass through as motion."""
        self.assertEqual(limit_velocity(float('nan'), float('inf'), 1.6, 1.0), (0.0, 0.0))
        self.assertEqual(limit_velocity(float('-inf'), 0.2, 1.6, 1.0), (0.0, 0.2))

    def test_bad_limits_are_rejected(self):
        """Verify nonpositive or nonfinite limits raise ValueError."""
        for bad in (0.0, -1.0, float('nan'), float('inf')):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    limit_velocity(0.1, 0.1, bad, 1.0)
                with self.assertRaises(ValueError):
                    limit_velocity(0.1, 0.1, 1.0, bad)


if __name__ == '__main__':
    unittest.main()
