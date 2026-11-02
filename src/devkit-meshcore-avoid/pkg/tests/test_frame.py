import pytest

from sowbot_avoid import ARRIVED, BEACON, ENTER, FINISHED, HELLO, Frame


def test_enter_example_bytes():
    f = Frame(2, 2, ENTER, 0, 2, False, (14, 15, 16, 17))
    assert f.encode().hex() == "0202410e000f0010001100"
    assert Frame.decode(f.encode()) == f
    assert f.hold() == (14, 15)
    assert f.claims() == ((15, 16), (16, 17))


def test_arrived_roundtrip():
    a = Frame(3, 9, ARRIVED, 3, 1, True, (5, 6))
    assert Frame.decode(a.encode()) == a
    assert a.hold() == (5, 5) and a.claims() == ((5, 6),)


def test_bad_frames_rejected():
    f = Frame(2, 2, ENTER, 0, 2, False, (14, 15, 16, 17))
    assert Frame.decode(b"\x01\x00") is None
    assert Frame.decode(f.encode()[:-1]) is None
    assert Frame.decode(b"\x00\x01\x00") is None       # robot id 0


@pytest.mark.parametrize(('kind', 'nodes', 'hold', 'claims'), [
    (HELLO, (), None, ()),
    (FINISHED, (), None, ()),
    (ENTER, (10, 11, 12, 13), (10, 11), ((11, 12), (12, 13))),
    (BEACON, (10, 11, 12, 13), (10, 11), ((11, 12), (12, 13))),
    (ARRIVED, (10, 11, 12), (10, 10), ((10, 11), (11, 12))),
])
def test_frame_kinds_encode_and_decode(kind, nodes, hold, claims):
    nclaims = max(0, len(nodes) - (2 if kind in (ENTER, BEACON) else 1))
    frame = Frame(255, 255, kind, 3, nclaims, True, nodes)
    decoded = Frame.decode(frame.encode())
    assert decoded == frame
    assert decoded.hold() == hold
    assert decoded.claims() == claims


@pytest.mark.parametrize('payload', [
    b'', b'\x01\x00', b'\x00\x00\x00',
    b'\x01\x00\x05',  # unknown kind
    b'\x01\x00\x60',  # three claims
    b'\x01\x00\x20',  # HELLO with a claim
    b'\x01\x00\x24',  # FINISHED with a claim
    b'\x01\x00\x01\x01\x00',  # incomplete ENTER
    b'\x01\x00\x00\x00\x00',  # extra node on HELLO
    b'\x01\x00\x03\x01\x00\x02',  # partial extra node
])
def test_invalid_frame_headers_and_lengths(payload):
    assert Frame.decode(payload) is None


def test_sequence_wrap_and_little_endian_node_boundaries():
    frame = Frame(1, 256, ENTER, nodes=(0, 65535))
    assert frame.encode() == b'\x01\x00\x01\x00\x00\xff\xff'
    assert Frame.decode(frame.encode()) == Frame(1, 0, ENTER, nodes=(0, 65535))


@pytest.mark.parametrize(('rid', 'node', 'error'), [(256, 0, ValueError), (1, -1, OverflowError),
                                                    (1, 65536, OverflowError)])
def test_encoding_out_of_range_values_fails(rid, node, error):
    with pytest.raises(error):
        Frame(rid, 0, ARRIVED, nodes=(node,)).encode()


def test_flags_and_node_order_match_wire_format():
    frame = Frame(7, 9, ARRIVED, resend=3, nclaims=2, retreat=True, nodes=(1, 256, 65535))
    payload = b'\x07\x09\xdb\x01\x00\x00\x01\xff\xff'
    assert frame.encode() == payload
    assert Frame.decode(payload) == frame
    assert frame.claims() == ((1, 256), (256, 65535))


@pytest.mark.parametrize('kind', [ENTER, BEACON, ARRIVED])
@pytest.mark.parametrize('nclaims', [0, 1, 2])
def test_decode_requires_exact_node_count(kind, nclaims):
    base_nodes = 2 if kind in (ENTER, BEACON) else 1
    frame = Frame(1, 42, kind, nclaims=nclaims, nodes=tuple(range(base_nodes + nclaims)))
    payload = frame.encode()
    assert Frame.decode(payload) == frame
    assert Frame.decode(payload[:-2]) is None
    assert Frame.decode(payload + b'\x00\x00') is None
