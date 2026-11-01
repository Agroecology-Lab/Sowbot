from sowbot_avoid import ARRIVED, ENTER, Frame


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
