"""Unit tests for the extracted recon-to-contour planning pipeline."""

import unittest
from unittest import mock

import numpy as np

from devkit_f2c_planner.f2c_planner import _f2c_xy_to_latlon
from devkit_ui import row_planning


class TestContourPipeline(unittest.TestCase):
    def setUp(self) -> None:
        """Keep projection real while isolating expensive DEM and planner stages."""
        self.anchor = (48.0046, 3.6644)
        self.corners = [self.anchor, (48.005, 3.6644), (48.0046, 3.665)]
        self.obstacles = [[(48.0047, 3.6645), (48.0048, 3.6645), (48.0047, 3.6646)]]
        self.xy = np.array([[25.0, -10.0], [-15.0, 20.0], [0.0, 0.0]])
        self.native_xy = self.xy + np.array([8000.0, -3000.0])
        self.latlon = np.array([_f2c_xy_to_latlon(x, y, *self.anchor) for x, y in self.xy])
        self.elevation = np.array([102.5, 107.0, 104.0])
        self.grid = np.array([[102.5, 104.0], [105.0, 107.0]])
        self.origin = (-15.0, -10.0)
        self.centroid = (12.0, 18.0)
        self.reference = [tuple(point) for point in self.latlon[:2]]
        self.swaths = [self.reference]
        self.load = mock.Mock(return_value=(self.native_xy, self.elevation, self.latlon))
        self.build = mock.Mock(return_value=(self.grid, self.origin, 0.01))
        self.find_centroid = mock.Mock(return_value=self.centroid)
        self.select = mock.Mock(return_value=self.reference)
        self.run = mock.Mock(return_value=self.swaths)
        self.enterContext(mock.patch.multiple(
            row_planning, load_recon_points=self.load, build_elevation_grid=self.build,
            field_centroid_xy=self.find_centroid, select_reference_contour_latlon=self.select,
            _run_contour_f2c=self.run,
        ))

    def plan(self, *, snake=False, **kwargs):
        """Use distinct non-default settings to expose lost or reordered arguments."""
        return row_planning.plan_contour_rows(
            self.corners, self.obstacles, 3.5, 0.4, 2.0, snake,
            'survey logs/recon.csv', 0.75, **kwargs,
        )

    def test_recon_is_reanchored_before_dem_and_contour_selection(self) -> None:
        """Ignore odometry-frame xy and share the field frame across every DEM stage."""
        original_xy = self.native_xy.copy()
        original_latlon = self.latlon.copy()

        self.assertIs(self.plan(), self.swaths)

        self.load.assert_called_once_with('survey logs/recon.csv')
        self.build.assert_called_once()
        xy, elevation, resolution = self.build.call_args.args
        np.testing.assert_allclose(xy, self.xy, atol=1e-6)
        self.assertIs(elevation, self.elevation)
        self.assertEqual(resolution, 0.75)
        self.find_centroid.assert_called_once_with(self.corners)
        self.select.assert_called_once()
        grid, resolution, origin, centroid, lat0, lon0 = self.select.call_args.args
        self.assertIs(grid, self.grid)
        self.assertEqual((resolution, origin, centroid, lat0, lon0),
                         (0.75, self.origin, self.centroid, *self.anchor))
        np.testing.assert_array_equal(self.native_xy, original_xy)
        np.testing.assert_array_equal(self.latlon, original_latlon)

    def test_all_planner_options_and_default_breaks_are_forwarded(self) -> None:
        """Obstacles, padding, headland width, and both traversal modes survive extraction."""
        for snake in (False, True):
            with self.subTest(snake=snake):
                self.run.reset_mock()

                self.assertIs(self.plan(snake=snake), self.swaths)

                self.run.assert_called_once_with(
                    self.corners, self.obstacles, self.reference, 3.5, 0.4, 2.0, snake,
                    break_after=None,
                )

    def test_fragment_breaks_are_written_back_to_the_callers_set(self) -> None:
        """The planner's fragment markers must reach the UI through the supplied set."""
        breaks = {7}

        def plan_fragments(*_args, break_after):
            self.assertIs(break_after, breaks)
            break_after.clear()
            break_after.add(0)
            return self.swaths

        self.run.side_effect = plan_fragments

        self.assertIs(self.plan(break_after=breaks), self.swaths)
        self.assertEqual(breaks, {0})

    def test_empty_plan_is_not_confused_with_flat_field_fallback(self) -> None:
        """No fitting swaths is a valid empty result, distinct from no reference contour."""
        empty_swaths = []
        self.run.return_value = empty_swaths

        self.assertIs(self.plan(), empty_swaths)
        self.run.assert_called_once()

    def test_flat_field_does_not_modify_fragment_breaks(self) -> None:
        """Fallback leaves the caller's markers intact and never calls the contour planner."""
        self.select.return_value = None
        breaks = {4}

        self.assertIsNone(self.plan(break_after=breaks))

        self.run.assert_not_called()
        self.assertEqual(breaks, {4})

    def test_pipeline_errors_propagate_unchanged_and_stop_downstream_work(self) -> None:
        """Malformed recon data and stage failures must not become a flat-field fallback."""
        stages = [self.load, self.build, self.find_centroid, self.select, self.run]
        failures = [
            (self.load, FileNotFoundError('missing recon.csv')),
            (self.load, PermissionError('recon.csv is not readable')),
            (self.load, KeyError('latitude')),
            (self.load, ValueError('too few recon samples')),
            (self.build, ValueError('grid exceeds cell limit')),
            (self.find_centroid, ValueError('invalid field boundary')),
            (self.select, ValueError('contour extraction failed')),
            (self.run, ValueError('invalid tool width')),
        ]
        for failed_stage, failure in failures:
            with self.subTest(error=str(failure)):
                for stage in stages:
                    stage.reset_mock(side_effect=True)
                failed_stage.side_effect = failure
                breaks = {2}

                with self.assertRaises(type(failure)) as raised:
                    self.plan(break_after=breaks)

                self.assertIs(raised.exception, failure)
                failed_stage.assert_called_once()
                for downstream in stages[stages.index(failed_stage) + 1:]:
                    downstream.assert_not_called()
                self.assertEqual(breaks, {2})

    def test_empty_boundary_stops_before_dem_construction(self) -> None:
        """A loaded recon log cannot supply the missing field anchor."""
        self.corners.clear()

        with self.assertRaises(IndexError):
            self.plan()

        self.load.assert_called_once_with('survey logs/recon.csv')
        self.build.assert_not_called()
        self.find_centroid.assert_not_called()
        self.select.assert_not_called()
        self.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
