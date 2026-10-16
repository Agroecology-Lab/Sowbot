"""Row geometry and contour planning for turning Fields2Cover swaths into topo rows.

These functions are pure: they take lat/lon or local xy coordinates and return
coordinates, with no ROS, NiceGUI or topology-map dependency. Keeping them out of
``ui_node`` lets them be imported and tested directly.
"""

import math
from itertools import pairwise

import numpy as np

from devkit_f2c_planner.f2c_planner import (
    f2c_latlon_to_xy,
    field_centroid_xy,
    run_contour_f2c,
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
# polyline that f2c_planner.run_contour_f2c() already produced, which is a
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

    ``points_ll`` is the row's full lat/lon point list as f2c_planner returns it,
    in degrees, as are ``anchor_lat`` and ``anchor_lon``.
    Waypoints are placed every ``interval_m`` meters of arc length, in a local
    frame anchored at ``anchor_lat``/``anchor_lon``, with x east and y north, in meters.
    Supply a finite, positive interval; zero raises ZeroDivisionError for two or more points.

    The row's first and last points are excluded, because callers already turn
    them into the IN and OUT topo nodes. Returns an empty list if the row has
    fewer than two points or is shorter than one interval. The final waypoint is
    dropped if it would sit within 0.3 * ``interval_m`` of the end, since a node
    that close to OUT only adds a near-zero-length last hop.
    """
    pts_xy = [f2c_latlon_to_xy(lat, lon, anchor_lat, anchor_lon)
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

    ``coords`` maps each row-end node name to its local (x, y).
    Consecutive nodes within each inferred field end are paired without modifying ``coords``.

    The IN/OUT label cannot be used to tell which end a node is at, because snake
    (boustrophedon) ordering flips the label-to-end mapping on alternate rows.
    Ends are inferred using the axis with the larger spread (y for a tie), split
    below versus at or above its upper median.
    Each group is ordered along the other axis.
    This assumes the chosen axis separates the physical row ends; wide or irregular
    fields can violate that assumption and produce pairs that cross rows.

    Used by TopologyEditService.stage_repair; initial row saving uses swath order instead.
    Returns an empty list for fewer than two endpoints, and never pairs a node
    with itself.
    Each pair is returned once; callers add any reverse edge.
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

    ``corners_ll`` and ``obstacle_rings`` contain (latitude, longitude) points in
    degrees; the field needs at least three corners.
    ``tool_width`` is the row spacing in meters.
    Positive ``pad_m`` expands obstacles; positive ``headland_m`` insets the field,
    falling back to the full boundary if the inset is empty or is not a single polygon.
    ``snake`` reverses alternate rows.
    ``dem_resolution_m`` is the finite, positive grid cell size in meters.

    Returns swaths as lists of (latitude, longitude) points in degrees, or an
    empty list if no rows survive planning.
    Returns None when no usable reference contour exists, including flat terrain,
    contours shorter than one meter, or
    a centroid outside the elevation grid (see dem.select_reference_contour_xy()).
    Callers should then fall back to straight swaths from ``run_f2c``.

    When a reference contour exists, ``break_after`` is cleared and filled in
    place with zero-based output indices after which headland links must be
    skipped because of field or obstacle gaps.
    It is unchanged on a None return.

    Reading ``recon_path`` propagates OSError (including FileNotFoundError),
    KeyError for missing CSV columns, and ValueError for invalid numbers or fewer
    than three samples.
    ValueError also propagates for invalid grid resolution or an oversized grid.
    Empty ``corners_ll`` raises IndexError after loading the CSV.
    Interpolation and uncaught geometry errors propagate; offset-curve
    failures are caught by the planner and stop further offsets on that side.
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
    xy = np.array([f2c_latlon_to_xy(lat, lon, lat0, lon0) for lat, lon in latlon])
    elevation_grid, origin_xy, _smoothing_used = build_elevation_grid(
        xy, elevation, dem_resolution_m)
    centroid_xy = field_centroid_xy(corners_ll)
    reference_line_ll = select_reference_contour_latlon(
        elevation_grid, dem_resolution_m, origin_xy, centroid_xy, lat0, lon0)
    if reference_line_ll is None:
        return None
    return run_contour_f2c(
        corners_ll, obstacle_rings, reference_line_ll, tool_width,
        pad_m, headland_m, snake, break_after=break_after)
