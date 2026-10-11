import math
import unittest
from unittest.mock import Mock, patch

from devkit_f2c_planner.f2c_planner import _f2c_xy_to_latlon
from devkit_ui import row_planning
from devkit_ui.row_planning import (
    headland_neighbour_pairs,
    plan_contour_rows,
    resample_row_xy,
)

ANCHOR_LAT, ANCHOR_LON = 51.0, -2.0


def row_from_xy(*points_xy: tuple[float, float]) -> list[tuple[float, float]]:
    """Build a lat/lon row from local xy points anchored at the test origin."""
    return [_f2c_xy_to_latlon(x, y, ANCHOR_LAT, ANCHOR_LON) for x, y in points_xy]


def resample(row: list, interval_m: float) -> list[tuple[float, float]]:
    return resample_row_xy(row, ANCHOR_LAT, ANCHOR_LON, interval_m)


def as_pairs(pairs: list) -> set[frozenset[str]]:
    """Compare node pairs without caring about the order within a pair."""
    return {frozenset(pair) for pair in pairs}


class TestResampleRowXy(unittest.TestCase):
    def assert_points_close(self, actual, expected) -> None:
        self.assertEqual(len(actual), len(expected), actual)
        for (ax, ay), (ex, ey) in zip(actual, expected, strict=True):
            self.assertAlmostEqual(ax, ex, places=3)
            self.assertAlmostEqual(ay, ey, places=3)

    def test_rows_with_fewer_than_two_points_have_no_waypoints(self) -> None:
        """Verify an empty row and a single point both yield no waypoints."""
        self.assertEqual(resample([], 2.5), [])
        self.assertEqual(resample(row_from_xy((0.0, 0.0)), 2.5), [])

    def test_row_shorter_than_one_interval_has_no_waypoints(self) -> None:
        """Verify a row shorter than the interval is left as a single entry-to-exit edge."""
        self.assertEqual(resample(row_from_xy((0.0, 0.0), (0.0, 2.0)), 2.5), [])

    def test_waypoints_are_evenly_spaced_and_exclude_the_ends(self) -> None:
        """Verify a straight 12 m row gets waypoints every 5 m, none at either end."""
        row = row_from_xy((0.0, 0.0), (0.0, 12.0))

        self.assert_points_close(resample(row, 5.0), [(0.0, 5.0), (0.0, 10.0)])

    def test_waypoint_too_close_to_the_exit_is_dropped(self) -> None:
        """Verify a waypoint within 0.3 intervals of the exit is dropped."""
        row = row_from_xy((0.0, 0.0), (0.0, 10.0))

        # Targets are 2.5, 5, 7.5 and 10. The last lands on the exit node itself.
        self.assert_points_close(resample(row, 2.5), [(0.0, 2.5), (0.0, 5.0), (0.0, 7.5)])

        # 10.5 m leaves 0.5 m after the last 2.5 m step, under the 0.75 m cut-off.
        row = row_from_xy((0.0, 0.0), (0.0, 10.5))
        self.assertEqual(len(resample(row, 2.5)), 3)

    def test_waypoints_follow_a_bend_in_the_row(self) -> None:
        """Verify arc length is measured along the polyline, not the straight chord."""
        row = row_from_xy((0.0, 0.0), (10.0, 0.0), (10.0, 10.0))

        self.assert_points_close(resample(row, 5.0), [(5.0, 0.0), (10.0, 0.0), (10.0, 5.0)])

    def test_waypoints_are_measured_from_the_row_not_the_anchor(self) -> None:
        """Verify spacing is the same wherever the row lies relative to the anchor."""
        row = row_from_xy((30.0, 40.0), (30.0, 52.0))
        points = resample(row, 5.0)

        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(math.dist(points[0], points[1]), 5.0, places=3)

    def test_exit_clearance_boundary_keeps_exactly_thirty_percent(self) -> None:
        """Keep the last waypoint at the clearance threshold, but not just below it."""
        # NOTE: Exact local coordinates isolate the boundary from projection roundoff.
        with patch.object(row_planning, '_f2c_latlon_to_xy', side_effect=lambda x, y, *_: (x, y)):
            for length, expected in (
                (22.999, [(10.0, 0.0)]),
                (23.0, [(10.0, 0.0), (20.0, 0.0)]),
                (23.001, [(10.0, 0.0), (20.0, 0.0)]),
            ):
                with self.subTest(length=length):
                    self.assertEqual(resample([(0.0, 0.0), (length, 0.0)], 10.0), expected)

    def test_repeated_vertices_do_not_change_waypoints(self) -> None:
        """Zero-length segments at the start, bend, and exit must not add nodes."""
        row = row_from_xy(
            (0.0, 0.0), (0.0, 0.0), (6.0, 0.0), (6.0, 0.0),
            (6.0, 6.0), (6.0, 6.0),
        )
        original = list(row)

        self.assert_points_close(resample(row, 3.0), [(3.0, 0.0), (6.0, 0.0), (6.0, 3.0)])
        self.assertEqual(row, original)

    def test_coincident_row_has_no_waypoints(self) -> None:
        """A stationary row cannot produce useful intermediate nodes."""
        self.assertEqual(resample(row_from_xy(*[(3.0, 4.0)] * 4), 2.5), [])

    def test_waypoints_accumulate_across_multiple_short_segments(self) -> None:
        """Spacing carries across vertices instead of restarting on each segment."""
        row = row_from_xy((0.0, 0.0), (1.0, 0.0), (1.0, 2.0), (3.0, 2.0), (3.0, 6.0))

        self.assert_points_close(resample(row, 4.0), [(2.0, 2.0)])

    def test_reversed_row_measures_spacing_from_its_new_entry(self) -> None:
        """Snake rows must emit waypoints in traversal order, including negative coordinates."""
        row = row_from_xy((-7.0, -3.0), (-7.0, 9.0))

        self.assert_points_close(resample(row[::-1], 5.0), [(-7.0, 4.0), (-7.0, -1.0)])

    def test_exactly_one_interval_does_not_duplicate_exit(self) -> None:
        """A row of one interval needs only the existing entry and exit nodes."""
        self.assertEqual(resample(row_from_xy((0.0, 0.0), (0.0, 2.5)), 2.5), [])


class TestHeadlandNeighbourPairs(unittest.TestCase):
    def test_shuffled_endpoints_are_sorted_geometrically_without_mutating_input(self) -> None:
        """Input order and translation must not introduce skip-links or cross-field edges."""
        coords = {
            'east_middle': (80.0, -7.0), 'west_last': (-20.0, -1.0),
            'east_last': (80.0, -1.0), 'west_first': (-20.0, -10.0),
            'east_first': (80.0, -10.0), 'west_middle': (-20.0, -7.0),
        }
        original = list(coords.items())
        expected = {
            frozenset({'west_first', 'west_middle'}),
            frozenset({'west_middle', 'west_last'}),
            frozenset({'east_first', 'east_middle'}),
            frozenset({'east_middle', 'east_last'}),
        }

        for endpoints in (coords, dict(reversed(original))):
            with self.subTest(order=list(endpoints)):
                pairs = headland_neighbour_pairs(endpoints)
                self.assertEqual(len(pairs), len(expected))
                self.assertEqual(as_pairs(pairs), expected)
        self.assertEqual(list(coords.items()), original)

    def test_single_row_does_not_connect_opposite_ends(self) -> None:
        """Without a neighbouring row there is no headland connection to create."""
        self.assertEqual(headland_neighbour_pairs({'IN': (-10.0, 0.0), 'OUT': (10.0, 0.0)}), [])

    def test_fewer_than_two_endpoints_have_no_pairs(self) -> None:
        """Verify there is nothing to join with zero or one endpoint."""
        self.assertEqual(headland_neighbour_pairs({}), [])
        self.assertEqual(headland_neighbour_pairs({'A': (0.0, 0.0)}), [])

    def test_two_rows_are_joined_at_each_end(self) -> None:
        """Verify matching ends of adjacent rows are paired."""
        coords = {'R1_IN': (0.0, 0.0), 'R1_OUT': (100.0, 0.0),
                  'R2_IN': (0.0, 3.0), 'R2_OUT': (100.0, 3.0)}

        self.assertEqual(
            as_pairs(headland_neighbour_pairs(coords)),
            {frozenset({'R1_IN', 'R2_IN'}), frozenset({'R1_OUT', 'R2_OUT'})})

    def test_snake_ordering_pairs_by_geometry_not_by_label(self) -> None:
        """Verify flipped IN/OUT labels on alternate rows do not change which ends pair up."""
        coords = {'R1_IN': (0.0, 0.0), 'R1_OUT': (100.0, 0.0),
                  'R2_IN': (100.0, 3.0), 'R2_OUT': (0.0, 3.0)}

        self.assertEqual(
            as_pairs(headland_neighbour_pairs(coords)),
            {frozenset({'R1_IN', 'R2_OUT'}), frozenset({'R1_OUT', 'R2_IN'})})

    def test_neighbours_are_chained_and_never_skip_linked(self) -> None:
        """Verify three rows give a chain at each end, without a link from the first to the last."""
        coords = {}
        for row, y in enumerate((0.0, 3.0, 6.0), start=1):
            coords[f'R{row}_IN'] = (0.0, y)
            coords[f'R{row}_OUT'] = (100.0, y)

        pairs = as_pairs(headland_neighbour_pairs(coords))

        self.assertEqual(pairs, {
            frozenset({'R1_IN', 'R2_IN'}), frozenset({'R2_IN', 'R3_IN'}),
            frozenset({'R1_OUT', 'R2_OUT'}), frozenset({'R2_OUT', 'R3_OUT'}),
        })

    def test_rows_running_north_to_south_are_paired_the_same_way(self) -> None:
        """Verify the long axis is detected, so rows along y pair along x."""
        coords = {'R1_IN': (0.0, 0.0), 'R1_OUT': (0.0, 100.0),
                  'R2_IN': (3.0, 0.0), 'R2_OUT': (3.0, 100.0)}

        self.assertEqual(
            as_pairs(headland_neighbour_pairs(coords)),
            {frozenset({'R1_IN', 'R2_IN'}), frozenset({'R1_OUT', 'R2_OUT'})})


class TestPlanContourRows(unittest.TestCase):
    CORNERS = ((ANCHOR_LAT, ANCHOR_LON), (51.003, -2.0), (ANCHOR_LAT, -1.999))

    def patch_pipeline(self, *, reference_line, run_contour=None) -> Mock:
        """Replace the DEM and planner stages so only plan_contour_rows' own wiring runs."""
        run_contour = run_contour or Mock(return_value=[])
        self.enterContext(patch.multiple(
            row_planning,
            load_recon_points=Mock(return_value=([], [1.0], [(ANCHOR_LAT, ANCHOR_LON)])),
            build_elevation_grid=Mock(return_value=([[1.0]], (0.0, 0.0), 0.0)),
            field_centroid_xy=Mock(return_value=(0.0, 0.0)),
            select_reference_contour_latlon=Mock(return_value=reference_line),
            _run_contour_f2c=run_contour,
        ))
        return run_contour

    def plan(self, **kwargs):
        return plan_contour_rows(list(self.CORNERS), [], 4.0, 0.0, 0.0, True, 'recon.csv', 1.0, **kwargs)

    def test_flat_field_returns_none_without_planning_swaths(self) -> None:
        """Verify no reference contour means None, so the caller can fall back to straight rows."""
        run_contour = self.patch_pipeline(reference_line=None)

        self.assertIsNone(self.plan())
        run_contour.assert_not_called()

    def test_break_after_is_passed_through_to_the_planner(self) -> None:
        """Verify the caller's break_after set reaches the contour planner."""
        swaths = [[(ANCHOR_LAT, ANCHOR_LON), (51.001, -2.0)]]
        reference = [(ANCHOR_LAT, ANCHOR_LON), (51.002, -2.0)]
        run_contour = self.patch_pipeline(
            reference_line=reference, run_contour=Mock(return_value=swaths))
        break_after: set[int] = set()

        self.assertEqual(self.plan(break_after=break_after), swaths)
        self.assertIs(run_contour.call_args.kwargs['break_after'], break_after)

    def test_missing_recon_log_is_raised_not_treated_as_a_flat_field(self) -> None:
        """Verify a missing recon log surfaces as an error instead of silently returning None."""
        self.patch_pipeline(reference_line=None)
        row_planning.load_recon_points.side_effect = FileNotFoundError('recon.csv')

        with self.assertRaises(FileNotFoundError):
            self.plan()


if __name__ == '__main__':
    unittest.main()
