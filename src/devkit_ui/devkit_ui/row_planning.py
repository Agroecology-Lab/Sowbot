"""Row geometry and contour planning for turning Fields2Cover swaths into topo rows.

These functions are pure: they take lat/lon or local xy coordinates and return
coordinates, with no ROS, NiceGUI or topology-map dependency. Keeping them out of
``ui_node`` lets them be imported and tested directly.
"""

import math
from itertools import pairwise

import numpy as np

from devkit_f2c_planner.f2c_planner import (
    _f2c_latlon_to_xy,
    _run_contour_f2c,
    field_centroid_xy,
)
from devkit_ui.dem import (
    build_elevation_grid,
    load_recon_points,
    select_reference_contour_latlon,
)

# Spacing, in metres, of the intermediate topo nodes dropped along curved rows.
# A single entry-to-exit edge gives limbic_row_follow nothing to track a bend
# with, so a curved row is chopped into short, near-straight hops.
#
# This sets hop length, not curve fidelity. Waypoints are interpolated along the
# polyline that f2c_planner._run_contour_f2c() already produced, which is a
# simplified offset of the reference contour (see simplify_tolerance_m in
# dem.select_reference_contour_xy(), 1.5x the DEM grid resolution by default).
# Between two of that polyline's vertices the row is a straight chord, so
# waypoints land on the chord rather than on the true elevation isoline. To track
# the terrain more tightly, lower "DEM grid resolution" in the Mission sidebar
# (which also tightens the tolerance) rather than shortening this interval: a
# denser chain along the same chord adds no information.
CONTOUR_WAYPOINT_INTERVAL_M = 2.5


def resample_row_xy(points_ll: list, anchor_lat: float, anchor_lon: float,
                    interval_m: float) -> list[tuple[float, float]]:
    """Return evenly spaced waypoints along a row, in local xy.

    ``points_ll`` is the row's full lat/lon point list as f2c_planner returns it.
    Waypoints are placed every ``interval_m`` of arc length, in a local frame
    anchored at ``anchor_lat``/``anchor_lon``.

    The row's first and last points are excluded, because callers already turn
    them into the IN and OUT topo nodes. Returns an empty list if the row has
    fewer than two points or is shorter than one interval. The final waypoint is
    dropped if it would sit within 0.3 * ``interval_m`` of the end, since a node
    that close to OUT only adds a near-zero-length last hop.
    """
    pts_xy = [_f2c_latlon_to_xy(lat, lon, anchor_lat, anchor_lon)
              for lat, lon in points_ll]
    if len(pts_xy) < 2:
        return []

    seg_lens = [math.dist(pts_xy[i], pts_xy[i + 1]) for i in range(len(pts_xy) - 1)]
    total_len = sum(seg_lens)
    if total_len < interval_m:
        return []

    targets = [interval_m * k for k in range(1, int(total_len // interval_m) + 1)]
    if targets and (total_len - targets[-1]) < 0.3 * interval_m:
        targets.pop()

    out_xy: list[tuple[float, float]] = []
    cum = 0.0
    seg_i = 0
    for target in targets:
        while seg_i < len(seg_lens) and cum + seg_lens[seg_i] < target:
            cum += seg_lens[seg_i]
            seg_i += 1
        if seg_i >= len(seg_lens):
            break
        frac = (target - cum) / seg_lens[seg_i] if seg_lens[seg_i] > 0 else 0.0
        x0, y0 = pts_xy[seg_i]
        x1, y1 = pts_xy[seg_i + 1]
        out_xy.append((x0 + frac * (x1 - x0), y0 + frac * (y1 - y0)))
    return out_xy



def headland_neighbour_pairs(coords: dict) -> list:
    """Return the node-name pairs to join with headland (nav_to_pose) edges.

    ``coords`` maps each row-end node name to its (x, y). Each node is linked
    only to its immediate neighbour at the same end of the field, so a route
    between rows follows the headland and never cuts across a crop row.

    The IN/OUT label cannot be used to tell which end a node is at, because snake
    (boustrophedon) ordering flips the label-to-end mapping on alternate rows.
    Ends are classified by geometry instead: rows are long, so the two ends lie at
    the extremes of the axis with the larger spread. The nodes are split into two
    ends on that axis, each end is ordered along the other axis, and consecutive
    nodes are paired. Neighbours are always chained, never skip-linked, so A*
    walks the headland instead of cutting a chord across a row mouth.

    save_f2c_rows_to_topo (initial build) and repair_row_connectivity (rewire)
    both use this, so the two cannot drift apart. Returns an empty list for fewer
    than two endpoints, and never pairs a node with itself.
    """
    pts = list(coords.items())
    if len(pts) < 2:
        return []
    xs = [p[1][0] for p in pts]
    ys = [p[1][1] for p in pts]
    end_idx   = 0 if (max(xs) - min(xs)) > (max(ys) - min(ys)) else 1
    along_idx = 1 - end_idx
    end_vals = sorted(p[1][end_idx] for p in pts)
    mid = end_vals[len(end_vals) // 2]
    end_lo = [p for p in pts if p[1][end_idx] <  mid]
    end_hi = [p for p in pts if p[1][end_idx] >= mid]
    out: list = []
    for group in (end_lo, end_hi):
        group.sort(key=lambda p: p[1][along_idx])
        for (a_name, _), (b_name, _) in pairwise(group):
            if a_name != b_name:
                out.append((a_name, b_name))
    return out


def plan_contour_rows(corners_ll: list, obstacle_rings: list, tool_width: float,
                      pad_m: float, headland_m: float, snake: bool,
                      recon_path: str, dem_resolution_m: float,
                      *, break_after: set[int] | None = None) -> list | None:
    """Plan contour-following swaths from a recon CSV, in one blocking call.

    Runs recon CSV -> reference contour -> contour swaths. It is CPU-bound
    (RBFInterpolator fit and swath offsetting), so callers run it off the event
    loop, for example through ``ng_run.io_bound``.

    Returns None, which is not an error, when the field is too flat for a usable
    reference contour (see dem.select_reference_contour_xy()). Callers should then
    fall back to straight swaths from ``_run_f2c``.

    Raises FileNotFoundError or ValueError, unchanged, from load_recon_points().
    A missing or too-short recon log is a setup mistake that should be reported to
    the user rather than silently treated as a flat field.
    """
    _xy_native, elevation, latlon = load_recon_points(recon_path)
    lat0, lon0 = corners_ll[0]
    # Recon points are logged in recon_dem_logger.py's own /odom-anchored
    # frame, unrelated to whatever frame the user's drawn boundary
    # (corners_ll) happens to be in. Re-anchor them onto corners_ll[0] via
    # their own lat/lon columns before building the elevation grid, so
    # origin_xy ends up in the same frame field_centroid_xy() computed
    # centroid_xy in below — without this, centroid_xy is checked against
    # an elevation grid built around a completely different, unrelated
    # local origin, which can easily land outside the grid entirely (this
    # is what produced the "centroid falls outside the elevation grid"
    # case with the France field-27 data: the fake India test field was
    # small enough that this mismatch went unnoticed by coincidence).
    xy = np.array([_f2c_latlon_to_xy(lat, lon, lat0, lon0) for lat, lon in latlon])
    elevation_grid, origin_xy, _smoothing_used = build_elevation_grid(
        xy, elevation, dem_resolution_m)
    centroid_xy = field_centroid_xy(corners_ll)
    reference_line_ll = select_reference_contour_latlon(
        elevation_grid, dem_resolution_m, origin_xy, centroid_xy, lat0, lon0)
    if reference_line_ll is None:
        return None
    return _run_contour_f2c(
        corners_ll, obstacle_rings, reference_line_ll, tool_width,
        pad_m, headland_m, snake, break_after=break_after)
