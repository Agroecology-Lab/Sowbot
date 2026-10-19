# pylint: disable=duplicate-code
"""
Obstacle management for the Sowbot webui.

Owns the obstacle list, its YAML persistence and the /obstacles publisher.
It reaches ROS only through the RosGateway it is given and reads the latest
GPS and odometry through two getters, so it holds no node or UI state. UI
state lives in devkit_ui.view_models.obstacle_view_model and the Nav and
Plan tab cards in devkit_ui.pages.obstacle_cards.

Threading model
---------------
self._obstacles is an immutable tuple. All writes replace it wholesale
under self._lock, and bump self._version in the same critical section.
Readers grab the tuple reference lock-free (CPython attribute reads are
atomic) and walk a consistent snapshot.

Two races are deliberately accepted:

  1. Status string: two concurrent writers can race on the status;
     last writer wins. The string is human-readable progress only, so a
     late "saved" message can briefly mask an earlier "writing…" — never
     misleading enough to matter.

  2. Topic vs file: _persist_and_publish snapshots under the lock, then
     writes the file, then publishes — both lock-free. An external topic
     observer can therefore briefly see a state newer than what's on
     disk (by milliseconds). The next save converges.

Both are commented at the relevant call sites.

Coordinate frames
-----------------
Obstacles are stored in WGS84 lat/lon — matches NavSatFix and Leaflet.
They are projected to the local map frame at publish time using the
(odom_xy ↔ gps_latlon) anchor from the latest odometry and GPS.
Without an anchor we cannot project, so we don't publish —
a costmap subscriber would otherwise treat raw lat/lon as metric and
place obstacles thousands of kilometres from the origin.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime

import yaml
from geometry_msgs.msg import Point32, PolygonStamped

from devkit_ui.qos import LATCHED_QOS

# ── Constants ────────────────────────────────────────────────────────────────

OBSTACLES_FILE = '/workspace/maps/obstacles.yaml'

_NAME_RE    = re.compile(r'^[A-Z0-9_]+$')
_NAME_CLEAN = re.compile(r'[^A-Z0-9_]')

# Latched profile, the same as the topology map's, so a costmap layer that
# subscribes late still receives the current obstacle set on connect.
_OBS_QOS = LATCHED_QOS

# WGS84 semi-major axis (m). Equirectangular projection is good to
# ~0.3% anywhere on the planet — comfortably inside metre-scale tolerance.
_R_EARTH = 6_378_137.0


# ── Geometry helpers (pure, module-level so they're easy to test) ────────────

def latlon_to_xy(lat: float, lon: float,
                 lat0: float, lon0: float) -> tuple[float, float]:
    """Convert (lat, lon) to local ENU (x, y) metres relative to (lat0, lon0)."""
    x = math.radians(lon - lon0) * _R_EARTH * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * _R_EARTH
    return x, y


def xy_to_latlon(x: float, y: float,
                 lat0: float, lon0: float) -> tuple[float, float]:
    """Inverse of latlon_to_xy."""
    lat = lat0 + math.degrees(y / _R_EARTH)
    lon = lon0 + math.degrees(x / (_R_EARTH * math.cos(math.radians(lat0))))
    return lat, lon


def circle_to_ring_ll(center_lat: float, center_lon: float,
                      radius_m: float) -> list[tuple[float, float]]:
    """Tessellate a circle into a closed WGS84 ring.
    Side count scales with radius, clamped to [16, 64]."""
    n = max(16, min(64, int(radius_m * 8)))
    cos_lat = math.cos(math.radians(center_lat))
    pts: list[tuple[float, float]] = []
    for i in range(n):
        a  = 2 * math.pi * i / n
        dx = radius_m * math.cos(a)
        dy = radius_m * math.sin(a)
        lat = center_lat + math.degrees(dy / _R_EARTH)
        lon = center_lon + math.degrees(dx / (_R_EARTH * cos_lat))
        pts.append((lat, lon))
    return pts


def obstacle_to_ring_ll(obs: dict) -> list[tuple[float, float]]:
    """Single obstacle dict → closed lat/lon ring, or [] for degenerate input."""
    t = obs.get('type')
    if t == 'circle':
        c   = obs.get('center', {})
        lat = c.get('lat')
        lon = c.get('lon')
        r   = float(obs.get('radius_m', 0.0))
        if lat is None or lon is None or r <= 0:
            return []
        return circle_to_ring_ll(lat, lon, r)
    if t == 'polygon':
        ring = [(p['lat'], p['lon']) for p in obs.get('points', [])
                if 'lat' in p and 'lon' in p]
        return ring if len(ring) >= 3 else []
    return []


def obstacles_to_rings_ll(
        obstacles) -> list[list[tuple[float, float]]]:
    """All obstacles → F2C-ready list of rings. Drops degenerate entries."""
    rings = [obstacle_to_ring_ll(o) for o in obstacles]
    return [r for r in rings if len(r) >= 3]


def validate_obstacle(kind: str, lat, lon, radius_m,
                      points_ll) ->str | None:
    """Validate add() inputs. Returns an error string or None.

    Module-level so tests don't have to instantiate ObstacleManager.
    """
    if kind == 'circle':
        if lat is None or lon is None:
            return 'ERROR: lat/lon required'
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            return 'ERROR: lat/lon must be numeric'
        if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
            return f'ERROR: lat/lon not finite ({lat}, {lon})'
        if abs(lat_f) < 1e-9 and abs(lon_f) < 1e-9:
            return 'ERROR: lat/lon are 0,0 (no fix?)'
        if radius_m <= 0:
            return 'ERROR: radius must be > 0'
        return None
    if kind == 'polygon':
        if not points_ll or len(points_ll) < 3:
            return 'ERROR: polygon needs 3+ corners'
        for la, lo in points_ll:
            if not (math.isfinite(float(la)) and math.isfinite(float(lo))):
                return f'ERROR: point not finite ({la}, {lo})'
        return None
    return f'ERROR: unknown kind {kind!r}'


# ── Manager ──────────────────────────────────────────────────────────────────

class ObstacleManager:
    """Owns the obstacle list, persistence, and the ROS publisher.

    Readers poll these properties:

        obstacles : tuple[dict, ...]   read-only snapshot
        version   : int                bumps on every change
        status    : str                last-action status (last writer wins)
    """

    def __init__(self, ros, get_odom: Callable[[], object | None],
                 get_gps: Callable[[], object | None],
                 path: str = OBSTACLES_FILE) -> None:
        """Store the RosGateway and the getters for the latest odometry and GPS fix.

        Nothing is loaded or published until start() is called.
        """
        self._ros        = ros
        self._get_odom   = get_odom
        self._get_gps    = get_gps
        self._path       = path
        self._lock       = threading.Lock()
        self._obstacles: tuple[dict, ...] = ()
        self._version:   int              = 0
        self._status:    str              = ''
        self._publisher                   = None
        # _last_anchor tracks whether we've ever successfully published.
        # Used by the cold-start timer to fire exactly once when GPS+odom
        # first become available, then never again (timer cancels itself).
        self._last_anchor: tuple | None = None
        self._anchor_timer                 = None

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Create the publisher, load saved obstacles in the background, and start the
        cold-start anchor watcher.

        Publisher, thread, and timer creation errors propagate to the caller;
        background load errors are caught by _load().
        """
        self._publisher = self._ros.create_publisher(
            PolygonStamped, '/obstacles', _OBS_QOS)

        threading.Thread(target=self._load, daemon=True).start()

        # Cold-start watcher: at 2 Hz, check if GPS+odom have arrived. As
        # soon as they have, publish whatever's loaded and cancel ourselves.
        # After the first publish, all subsequent updates flow through
        # _persist_and_publish, which calls _publish directly.
        self._anchor_timer = self._ros.create_timer(
            0.5, self._cold_start_publish)

    @property
    def obstacles(self) -> tuple[dict, ...]:
        """Return the current obstacle snapshot."""
        return self._obstacles

    @property
    def version(self) -> int:
        """Return a counter that increases on every change to the obstacles."""
        return self._version

    @property
    def status(self) -> str:
        """Return the last action's status text; an error starts with 'ERROR'."""
        return self._status

    def set_status(self, s: str) -> None:
        """Set the status text. Last writer wins; the race is accepted (see module docs)."""
        self._status = s

    # ── public API ────────────────────────────────────────────────────────

    def add(self, kind: str, *,
            lat: float | None = None,
            lon: float | None = None,
            radius_m: float = 0.5,
            points_ll: list | None = None,
            name: str = '') ->str | None:
        """Add a circle or polygon using WGS84 coordinates in degrees.

        Circles use lat/lon and a positive radius_m in meters; polygons use
        at least three (latitude, longitude) pairs in points_ll.
        Normalize name to uppercase letters, digits, and underscores; allocate
        an unused OBS_N name when the normalized name is empty.

        Return the allocated name after updating memory and starting background
        persistence and publication.
        Return None for validation or name rejection, with the reason in status.
        A returned name does not confirm a disk save;
        persistence failures set an error status without undoing the addition.
        Malformed polygon coordinates can raise TypeError or ValueError during
        validation; errors from rounding coordinates or starting the thread also propagate.
        """

        err = validate_obstacle(kind, lat, lon, radius_m, points_ll)
        if err:
            self.set_status(err)
            return None

        clean = _NAME_CLEAN.sub(
            '', (name or '').strip().upper().replace(' ', '_'))
        timestamp = datetime.now(UTC).strftime('%d-%m-%Y_%H-%M-%S')

        # Lock covers name allocation + tuple replacement + version bump.
        # Disk write and publish happen after release, in a thread.
        with self._lock:
            existing = {o.get('name') for o in self._obstacles}
            if not clean:
                i = 1
                while f'OBS_{i}' in existing:
                    i += 1
                clean = f'OBS_{i}'
            elif not _NAME_RE.match(clean):
                self.set_status(f'ERROR: invalid name {clean!r}')
                return None
            elif clean in existing:
                self.set_status(f'ERROR: {clean} already exists')
                return None

            if kind == 'circle':
                obs = {
                    'name':       clean,
                    'type':       'circle',
                    'center':     {'lat': round(lat, 7), 'lon': round(lon, 7)},
                    'radius_m':   round(float(radius_m), 2),
                    'dropped_by': 'webui',
                    'timestamp':  timestamp,
                }
                desc = f'@ ({lat:.5f}, {lon:.5f}) r={radius_m}m'
            else:
                obs = {
                    'name':       clean,
                    'type':       'polygon',
                    'points':     [{'lat': round(la, 7), 'lon': round(lo, 7)}
                                   for la, lo in points_ll],
                    'dropped_by': 'webui',
                    'timestamp':  timestamp,
                }
                desc = f'({len(points_ll)} pts)'

            self._obstacles = (*self._obstacles, obs)
            self._version  += 1

        self.set_status(f'{clean} {desc} — writing…')
        threading.Thread(
            target=self._persist_and_publish,
            args=(f'{clean} saved · {len(self._obstacles)} total',),
            daemon=True).start()
        return clean

    def delete(self, name: str) -> bool:
        """Remove matching obstacles from memory and start background saving and publication.

        Return False and set an error status if name is absent; otherwise return True.
        Persistence failures set an error status without restoring the obstacles.
        Thread startup errors propagate after the in-memory removal.
        """
        with self._lock:
            if not any(o.get('name') == name for o in self._obstacles):
                self.set_status(f'ERROR: {name!r} not found')
                return False
            self._obstacles = tuple(o for o in self._obstacles
                                    if o.get('name') != name)
            self._version  += 1

        self.set_status(f'deleting {name} — writing…')
        threading.Thread(
            target=self._persist_and_publish,
            args=(f'deleted {name} · {len(self._obstacles)} left',),
            daemon=True).start()
        return True

    def rings_ll(self) -> list[list[tuple[float, float]]]:
        """F2C-ready snapshot. Safe to call from any thread."""
        return obstacles_to_rings_ll(self._obstacles)

    # ── internals ─────────────────────────────────────────────────────────

    # ── persistence ───────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load the YAML obstacle snapshot, update state, and attempt publication.

        Leave state unchanged when the file is missing.
        Load errors are caught; publication is skipped without a usable GPS/odometry anchor.
        """
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path, encoding='utf-8') as fh:
                doc = yaml.safe_load(fh) or {}
            loaded = doc.get('obstacles', []) or []
            with self._lock:
                self._obstacles = tuple(loaded)
                self._version  += 1
            self._ros.get_logger().info(
                f'Loaded {len(loaded)} obstacles from {self._path}')
            # Try to publish immediately. May no-op if no GPS yet — the
            # cold-start timer will catch that case.
            self._publish()
        except Exception as e:
            self._ros.get_logger().warn(f'Failed to load obstacles: {e}')

    def _persist_and_publish(self, success_msg: str) -> None:
        """Atomic write (tmp + os.replace), then publish.

        Snapshot under lock, write + publish lock-free. The topic can
        briefly reflect a state newer than disk (by ms) — see module docs.
        Persistence errors are caught and reported in the status.
        """
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + '.tmp'
            with self._lock:
                snapshot = list(self._obstacles)
            with open(tmp, 'w', encoding='utf-8') as fh:
                yaml.dump({'obstacles': snapshot}, fh,
                          default_flow_style=False, sort_keys=False,
                          allow_unicode=True)
            os.replace(tmp, self._path)
            self.set_status(success_msg)
            self._ros.get_logger().info(f'Obstacles saved: {len(snapshot)} total')
            self._publish()
        except Exception as e:
            self.set_status(f'ERROR: {e}')
            self._ros.get_logger().error(f'persist failed: {e}')

    # ── publishing ────────────────────────────────────────────────────────

    def _current_anchor(self) -> tuple | None:
        """Return (map_x, map_y, lat, lon), in meters and degrees, for projection.

        Return None if either message is missing, GPS coordinates are nonfinite,
        or both GPS coordinates have magnitude below 1e-9 degrees.
        Odometry coordinates are returned without validation.
        Getter errors and errors accessing or checking message fields propagate.
        """
        odom = self._get_odom()
        gps  = self._get_gps()
        if odom is None or gps is None:
            return None
        lat, lon = gps.latitude, gps.longitude
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None
        if abs(lat) < 1e-9 and abs(lon) < 1e-9:
            return None
        return (odom.pose.pose.position.x, odom.pose.pose.position.y,
                lat, lon)

    def _publish(self) -> None:
        """Publish each obstacle as a PolygonStamped in the map frame.
        Silent no-op if there's no publisher or projection anchor.

        Anchor lookup errors propagate.
        Conversion and publication errors are caught, ending the attempt after
        any polygons already published.
        """
        if self._publisher is None:
            return
        anchor = self._current_anchor()
        if anchor is None:
            return
        ax, ay, alat, alon = anchor

        try:
            snapshot = self._obstacles    # tuple read is atomic in CPython
            stamp    = self._ros.get_clock().now().to_msg()
            for obs in snapshot:
                ring_ll = obstacle_to_ring_ll(obs)
                if not ring_ll:
                    continue
                msg = PolygonStamped()
                msg.header.stamp    = stamp
                msg.header.frame_id = 'map'
                for lat, lon in ring_ll:
                    mx, my = latlon_to_xy(lat, lon, alat, alon)
                    p = Point32()
                    p.x = float(ax + mx)
                    p.y = float(ay + my)
                    p.z = 0.0
                    msg.polygon.points.append(p)
                self._publisher.publish(msg)
            self._last_anchor = anchor
        except Exception as e:
            self._ros.get_logger().warn(f'publish failed: {e}')

    def _cold_start_publish(self) -> None:
        """Run by a 2 Hz timer during cold start. Publishes once GPS+odom
        arrive, then cancels itself — all subsequent updates flow through
        _persist_and_publish."""
        if self._last_anchor is not None:
            self._cancel_anchor_timer()
            return
        if not self._obstacles:
            return   # nothing to publish yet
        if self._current_anchor() is None:
            return   # GPS / odom not ready
        self._publish()
        if self._last_anchor is not None:
            self._cancel_anchor_timer()

    def _cancel_anchor_timer(self) -> None:
        if self._anchor_timer is not None:
            try:
                self._anchor_timer.cancel()
            except Exception:
                logging.getLogger(__name__).debug('could not cancel anchor timer', exc_info=True)
            self._anchor_timer = None
