"""upload_fence.py against a scripted stand-in for the flight controller's mission protocol."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('pymavlink')
from pymavlink import mavutil

MAV = mavutil.mavlink
FARM_MAP = Path(__file__).parent / 'farm-map.geojson'
SQUARE = [(51.0, -2.0), (51.0, -1.999), (51.001, -1.999), (51.001, -2.0)]


@pytest.fixture(scope='module')
def tool():
    """Load tools/upload_fence.py as a module."""
    spec = importlib.util.spec_from_file_location(
        'upload_fence', Path(__file__).parents[1] / 'tools' / 'upload_fence.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeController:
    """Answers the mission protocol: requests each item in turn, acks, and serves items back."""

    def __init__(self, refuse=None, silent=False, stored_items=None, first_request=0):
        self.target_system, self.target_component = 1, 1
        self.refuse, self.silent, self.first_request = refuse, silent, first_request
        self.inbox, self.items, self.expected = [], dict(stored_items or {}), 0
        self.mav = SimpleNamespace(
            mission_count_send=self._count, mission_item_int_send=self._item,
            mission_request_list_send=self._list, mission_request_int_send=self._request,
            mission_ack_send=lambda *a, **k: None)

    def _say(self, kind, **fields):
        self.inbox.append(SimpleNamespace(get_type=lambda: kind, **fields))

    def _count(self, _sys, _comp, count, mission_type):
        assert mission_type == MAV.MAV_MISSION_TYPE_FENCE
        self.expected, self.items = count, {}
        if self.silent:
            return
        self._say('MISSION_REQUEST_INT', seq=self.first_request)

    def _item(self, _sys, _comp, seq, frame, command, _cur, _auto, p1, _p2, _p3, _p4, x, y, _z, mission_type):
        assert mission_type == MAV.MAV_MISSION_TYPE_FENCE
        assert command == MAV.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION
        assert p1 == self.expected  # every vertex carries the vertex count
        self.items[seq] = SimpleNamespace(command=command, x=x, y=y)
        if seq + 1 < self.expected:
            self._say('MISSION_REQUEST_INT', seq=seq + 1)
        else:
            self._say('MISSION_ACK', type=self.refuse if self.refuse is not None else MAV.MAV_MISSION_ACCEPTED)

    def _list(self, *_args):
        self._say('MISSION_COUNT', count=len(self.items))

    def _request(self, _sys, _comp, seq, _mission_type):
        self._say('MISSION_ITEM_INT', **vars(self.items[seq]))

    def recv_match(self, type=None, blocking=True, timeout=None):
        """Return the next queued message of a wanted type, or None as a timeout would."""
        for index, message in enumerate(self.inbox):
            if message.get_type() in type:
                return self.inbox.pop(index)
        return None


def test_polygon_is_selected_by_role_and_field(tool):
    """Verify the field polygon comes out as (lat, lon) without its closing vertex."""
    document = json.loads(FARM_MAP.read_text())
    vertices = tool.load_polygon(document, 'field', 1)
    ring = next(f for f in document['features'] if f['properties'].get('field') == 1
                and f['geometry']['type'] == 'Polygon')['geometry']['coordinates'][0]
    assert len(vertices) == len(ring) - 1
    assert vertices[0] == (ring[0][1], ring[0][0])


def test_ambiguous_or_missing_polygon_is_refused(tool):
    """Verify two fields without --field, an unknown field and an absent role all stop."""
    document = json.loads(FARM_MAP.read_text())
    for role, field in (('field', None), ('field', 99), ('fence', None)):
        with pytest.raises(tool.FenceError):
            tool.load_polygon(document, role, field)


def test_swapped_coordinates_are_caught(tool):
    """Verify a [lat, lon] file (latitude beyond 90) is rejected rather than uploaded."""
    document = {'features': [{'geometry': {'type': 'Polygon', 'coordinates': [[[51, 120], [51, 121], [52, 121]]]},
                              'properties': {'role': 'fence'}}]}
    with pytest.raises(tool.FenceError, match='latitude'):
        tool.load_polygon(document, 'fence')


def test_upload_then_readback_round_trips(tool):
    """Verify every vertex is sent as degE7 and the same polygon comes back."""
    controller = FakeController()
    tool.upload(controller, SQUARE)
    stored = tool.download(controller)
    tool.verify(SQUARE, stored)
    assert controller.items[0].x == 510000000 and controller.items[0].y == -20000000


def test_a_refused_upload_raises(tool):
    """Verify a MISSION_ACK other than accepted is an error."""
    with pytest.raises(tool.FenceError, match='refused'):
        tool.upload(FakeController(refuse=MAV.MAV_MISSION_ERROR), SQUARE)


def test_an_out_of_range_request_raises(tool):
    """Verify a request for a vertex that does not exist is an error and not an IndexError."""
    with pytest.raises(tool.FenceError, match='asked for vertex 9'):
        tool.upload(FakeController(first_request=9), SQUARE)


def test_a_silent_controller_times_out(tool):
    """Verify no reply is an error and not a hang or a silent success."""
    with pytest.raises(tool.FenceError, match='timed out'):
        tool.upload(FakeController(silent=True), SQUARE)


def test_verify_detects_a_changed_or_missing_vertex(tool):
    """Verify readback must match what was sent."""
    with pytest.raises(tool.FenceError, match='sent 4'):
        tool.verify(SQUARE, SQUARE[:3])
    moved = [*SQUARE[:3], (SQUARE[3][0] + 0.0001, SQUARE[3][1])]
    with pytest.raises(tool.FenceError, match='vertex 3'):
        tool.verify(SQUARE, moved)


def test_other_stored_items_are_not_mistaken_for_the_fence(tool):
    """Verify a stored exclusion or circle stops the readback instead of being treated as ours."""
    other = {0: SimpleNamespace(command=MAV.MAV_CMD_NAV_FENCE_CIRCLE_INCLUSION, x=0, y=0)}
    with pytest.raises(tool.FenceError, match='different item'):
        tool.download(FakeController(stored_items=other))


def test_dry_run_prints_and_does_not_connect(tool, capsys):
    """Verify --dry-run needs no endpoint and exits cleanly."""
    assert tool.main(['--geojson', str(FARM_MAP), '--role', 'field', '--field', '1', '--dry-run']) == 0
    assert 'vertices' in capsys.readouterr().out


def test_upload_without_an_endpoint_is_an_error(tool, capsys):
    """Verify the tool refuses to guess a link."""
    assert tool.main(['--geojson', str(FARM_MAP), '--role', 'field', '--field', '1']) == 1
    assert '--endpoint' in capsys.readouterr().err


@pytest.mark.parametrize('closed', [False, True])
def test_polygon_accepts_altitude_and_optional_closing_vertex(tool, closed):
    """GeoJSON altitude must not alter the lat/lon polygon or add a duplicate vertex."""
    ring = [[lon, lat, 100] for lat, lon in SQUARE]
    if closed:
        ring.append(ring[0])
    document = {'features': [{'properties': {'role': 'fence'},
                              'geometry': {'type': 'Polygon', 'coordinates': [ring]}}]}
    assert tool.load_polygon(document, 'fence') == SQUARE


@pytest.mark.parametrize('ring', [[], [[0, 0]], [[0, 0], [1, 1]], [[0, 0], [1, 1], [0, 0]]])
def test_polygon_requires_three_vertices_after_removing_closure(tool, ring):
    """A repeated closing point cannot turn a two-point line into a fence."""
    document = {'features': [{'properties': {'role': 'fence'},
                              'geometry': {'type': 'Polygon', 'coordinates': [ring]}}]}
    with pytest.raises(tool.FenceError, match='at least three'):
        tool.load_polygon(document, 'fence')


@pytest.mark.parametrize('point', [[181, 0], [-181, 0], [0, -91], [float('nan'), 0], [0, float('inf')]])
def test_polygon_rejects_invalid_coordinates(tool, point):
    """Reject out-of-range and nonfinite coordinates before touching the controller."""
    document = {'features': [{'properties': {'role': 'fence'},
                              'geometry': {'type': 'Polygon', 'coordinates': [[point, [1, 1], [2, 2]]]}}]}
    with pytest.raises(tool.FenceError, match='outside valid'):
        tool.load_polygon(document, 'fence')


@pytest.mark.parametrize('axis', [0, 1])
def test_verify_allows_rounding_but_rejects_larger_coordinate_changes(tool, axis):
    """The tolerance applies independently to latitude and longitude."""
    for offset, accepted in [(1e-7, True), (2e-7, False)]:
        stored = [0.0, 0.0]
        stored[axis] = offset
        if accepted:
            tool.verify([(0.0, 0.0)], [tuple(stored)])
        else:
            with pytest.raises(tool.FenceError, match='vertex 0'):
                tool.verify([(0.0, 0.0)], [tuple(stored)])


def test_download_timeout_does_not_acknowledge_incomplete_readback(tool, monkeypatch):
    """A missing vertex must fail without acknowledging a completed download."""
    from unittest.mock import Mock

    controller = FakeController(stored_items={0: SimpleNamespace()})
    ack = Mock()
    monkeypatch.setattr(controller.mav, 'mission_ack_send', ack)
    monkeypatch.setattr(controller.mav, 'mission_request_int_send', lambda *args: None)
    with pytest.raises(tool.FenceError, match='MISSION_ITEM_INT'):
        tool.download(controller)
    ack.assert_not_called()


def test_connect_ignores_ground_stations_and_learns_controller_ids(tool, monkeypatch):
    """Only a real autopilot heartbeat may select the upload target."""
    from unittest.mock import Mock

    controller = Mock()
    controller.recv_match.side_effect = [
        SimpleNamespace(type=MAV.MAV_TYPE_GCS),
        SimpleNamespace(type=MAV.MAV_TYPE_GROUND_ROVER, autopilot=MAV.MAV_AUTOPILOT_INVALID),
        SimpleNamespace(type=MAV.MAV_TYPE_GROUND_ROVER, autopilot=MAV.MAV_AUTOPILOT_ARDUPILOTMEGA,
                        get_srcSystem=lambda: 7, get_srcComponent=lambda: 3),
    ]
    connect = Mock(return_value=controller)
    monkeypatch.setattr(mavutil, 'mavlink_connection', connect)
    monkeypatch.setattr(tool.time, 'monotonic', lambda: 0.0)
    assert tool._connect('synthetic-endpoint', 57600, 10.0) is controller
    connect.assert_called_once_with('synthetic-endpoint', baud=57600)
    assert (controller.target_system, controller.target_component) == (7, 3)


def test_connect_timeout_reports_missing_controller(tool, monkeypatch):
    """A heartbeat deadline expires without a real serial wait."""
    from unittest.mock import Mock

    monkeypatch.setattr(mavutil, 'mavlink_connection', Mock())
    ticks = iter([0.0, 11.0])
    monkeypatch.setattr(tool.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(tool.FenceError, match='no flight controller heartbeat'):
        tool._connect('synthetic-endpoint', 115200, 10.0)


def test_main_uploads_and_verifies_controller_readback(tool, monkeypatch, capsys):
    """The CLI succeeds only after uploading and reading the same fence back."""
    controller = FakeController()
    monkeypatch.setattr(tool, '_connect', lambda *args: controller)
    assert tool.main(['--geojson', str(FARM_MAP), '--role', 'field', '--field', '1',
                      '--endpoint', 'synthetic-endpoint']) == 0
    assert len(controller.items) >= 3
    assert 'uploaded and read back' in capsys.readouterr().out


def test_main_readback_mismatch_returns_failure(tool, monkeypatch, capsys):
    """An accepted upload alone must never produce the CLI success message."""
    monkeypatch.setattr(tool, '_connect', lambda *args: FakeController())
    monkeypatch.setattr(tool, 'download', lambda *args: [])
    assert tool.main(['--geojson', str(FARM_MAP), '--role', 'field', '--field', '1',
                      '--endpoint', 'synthetic-endpoint']) == 1
    output = capsys.readouterr()
    assert 'controller holds 0' in output.err
    assert 'uploaded and read back' not in output.out
