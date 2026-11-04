"""Side-step: nearest free row waypoint, never an _IN or _OUT node, never a row another robot announced."""
from unittest.mock import AsyncMock

from sowbot_avoid import Agent, Frame, Graph
from sowbot_avoid.radio import Radio


class FixedRandom:
    def uniform(self, low, high):
        return (low + high) / 2


# Three rows 2 m apart, 3 m between nodes along a row: IN, W1, W2, OUT. Headland edges join IN-IN and OUT-OUT.
#   row 1: 1 IN, 2 W1, 3 W2, 4 OUT     row 2: 5 IN, 6 W1, 7 W2, 8 OUT     row 3: 9 IN, 10 W1, 11 W2, 12 OUT
def field():
    xy, edges, row_of, stops = {}, [], {}, set()
    for r in range(3):
        ids = [4 * r + k for k in (1, 2, 3, 4)]
        for k, n in enumerate(ids):
            xy[n], row_of[n] = (3.0 * k, 2.0 * r), r + 1
        edges += list(zip(ids, ids[1:]))
        stops |= {ids[1], ids[2]}
        if r:
            edges += [(ids[0] - 4, ids[0]), (ids[3] - 4, ids[3])]
    return Graph(edges, xy, 1.0, rows=stops, row_of=row_of)


def agent(route=(5, 6, 7)):
    return Agent(2, route, field(), Radio(), AsyncMock(), rng=FixedRandom())


def hear(a, rid, node):
    a.book[rid] = Frame(rid, 0, (node,))
    a.seen[rid] = 0


def test_goes_to_the_nearest_waypoint_of_a_row_nobody_announced():
    a = agent()
    hear(a, 3, 7)                                   # row 2 is announced
    assert a._sidestep()
    assert a.route[:3] == [5, 1, 2]                 # through IN of row 1, stops on its first waypoint
    assert a.hold_idx == 2 and a.route[a.hold_idx] == 2


def test_announced_row_is_skipped_and_the_next_row_is_taken():
    a = agent()
    hear(a, 3, 7)
    hear(a, 4, 3)                                   # row 1 is announced too
    assert a._sidestep()
    assert a.route[:3] == [5, 9, 10]                # row 3, through its IN node


def test_never_stops_on_an_in_or_out_node():
    a = agent()
    hear(a, 3, 7)
    hear(a, 4, 3)
    hear(a, 5, 11)                                  # every row announced; only IN and OUT nodes are left
    before = list(a.route)
    assert not a._sidestep()
    assert a.route == before and a.sidesteps == 0


def test_any_announced_node_of_a_row_takes_the_whole_row():
    a = agent()
    hear(a, 3, 4)                                   # row 1 announced at its OUT node, far from the stop
    hear(a, 4, 7)
    assert a._sidestep()
    assert a.route[:3] == [5, 9, 10]


def test_waits_at_the_stop_until_the_route_is_announced_clear():
    a = agent()                                     # robot id 2
    hear(a, 3, 7)
    assert a._sidestep()
    hear(a, 1, 7)                                   # a lower id sits on the way back
    assert a._route_blocked() == {1}
    hear(a, 1, 12)
    assert a._route_blocked() == set()


def test_higher_ids_are_not_waited_for_at_the_stop():
    a = agent()
    hear(a, 3, 7)
    assert a._sidestep()
    assert a._route_blocked() == set()              # robot 3 yields to us


def test_stop_sends_no_claims_until_the_route_is_clear():
    a = agent()
    hear(a, 3, 7)
    assert a._sidestep()
    a.idx = a.hold_idx
    a.cur = a.route[a.idx]
    sent = []
    a.radio.send = AsyncMock(side_effect=lambda p: sent.append(Frame.decode(p)))
    import asyncio
    asyncio.run(a._announce())
    assert sent[0].nodes == (a.cur,) and sent[0].claims() == ()
