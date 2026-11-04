import pytest

from sowbot_avoid import Frame


def test_example_bytes_and_roundtrip():
    f = Frame(2, 9, (14, 15, 16))
    assert f.encode().hex() == "02090e000f001000"
    assert Frame.decode(f.encode()) == f
    assert f.hold() == (14, 14)
    assert f.claims() == ((14, 15), (15, 16))


@pytest.mark.parametrize(('nodes', 'claims'), [
    ((10,), ()),
    ((10, 11), ((10, 11),)),
    ((10, 11, 12), ((10, 11), (11, 12))),
])
def test_one_to_three_nodes(nodes, claims):
    f = Frame(255, 255, nodes)
    assert len(f.encode()) == 2 + 2 * len(nodes)
    assert Frame.decode(f.encode()) == f
    assert f.hold() == (nodes[0], nodes[0]) and f.claims() == claims


@pytest.mark.parametrize('payload', [
    b'', b'\x01', b'\x01\x00',                      # no node
    b'\x00\x00\x01\x00',                            # robot id 0
    b'\x01\x00\x01',                                # odd length
    b'\x01\x00\x01\x00\x02',                        # partial node
    b'\x01\x00' + b'\x01\x00' * 4,                  # four nodes
])
def test_invalid_payloads_rejected(payload):
    assert Frame.decode(payload) is None


def test_sequence_wrap_and_little_endian_node_boundaries():
    frame = Frame(1, 256, (0, 65535))
    assert frame.encode() == b'\x01\x00\x00\x00\xff\xff'
    assert Frame.decode(frame.encode()) == Frame(1, 0, (0, 65535))
    assert Frame.decode(b'\x07\x09\x01\x00\x00\x01').nodes == (1, 256)


@pytest.mark.parametrize(('rid', 'node', 'error'), [(256, 0, ValueError), (1, -1, OverflowError),
                                                    (1, 65536, OverflowError)])
def test_encoding_out_of_range_values_fails(rid, node, error):
    with pytest.raises(error):
        Frame(rid, 0, (node,)).encode()
