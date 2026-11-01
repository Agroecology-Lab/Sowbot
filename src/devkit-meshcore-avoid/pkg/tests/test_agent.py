import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from sowbot_avoid import ARRIVED, ENTER, FINISHED, HELLO, Agent, Frame, Graph
from sowbot_avoid import agent as agent_module
from sowbot_avoid.radio import Radio


class FixedRandom:
    def uniform(self, low, high):
        return (low + high) / 2


def make_agent(route=(1, 2, 4), graph=None, **kwargs):
    graph = graph or Graph([(1, 2), (1, 3), (2, 3), (2, 4)])
    return Agent(2, route, graph, Radio(), AsyncMock(), rng=FixedRandom(), **kwargs)


def test_receive_rejects_invalid_own_duplicate_and_old_frames():
    agent = make_agent()
    agent.on_rx(b'bad')
    agent.on_rx(Frame(2, 1, HELLO).encode())
    assert agent.book == {} and not agent.wake.is_set()
    agent.on_rx(Frame(1, 255, HELLO).encode())
    assert agent.book[1].seq == 255 and agent.wake.is_set()
    agent.wake.clear()
    for seq in (255, 254, 127):  # duplicate, backwards, half-range ambiguity
        agent.on_rx(Frame(1, seq, HELLO).encode())
    assert agent.book[1].seq == 255 and not agent.wake.is_set()
    agent.on_rx(Frame(1, 0, FINISHED).encode())
    assert agent.book[1].kind == FINISHED and agent.book[1].seq == 0
    assert agent.wake.is_set()


def test_holds_claim_priority_and_finished_frames():
    agent = make_agent()
    agent.book = {
        1: Frame(1, 0, ARRIVED, nclaims=1, nodes=(3, 2)),
        3: Frame(3, 0, ARRIVED, nclaims=1, nodes=(4, 2)),
        4: Frame(4, 0, ENTER, nodes=(2, 5)),
        5: Frame(5, 0, FINISHED),
    }
    assert agent.blocked_by((1, 2)) == {1, 4}
    agent.retreating = True
    assert agent.blocked_by((1, 2)) == {1, 3, 4}
    agent.book = {1: Frame(1, 0, ARRIVED, nclaims=1, nodes=(3, 1))}
    assert agent.blocked_by((1, 2)) == set()  # claims containing our current node are ignored
    assert agent.mutual(1)
    assert not agent.mutual(99)


def test_announce_wraps_sequence_schedules_repeats_and_supersedes(monkeypatch):
    monkeypatch.setattr(agent_module.time, 'monotonic', lambda: 100)
    agent = make_agent(scale=2)
    agent.seq = 255
    agent.announce(ENTER, (1, 2), repeats=(1, 3))
    assert [(t, f.seq, f.resend) for t, f in agent.q] == [(100, 0, 0), (102, 0, 1), (106, 0, 2)]
    agent.announce(FINISHED, (), repeats=(1,))
    agent.announce(HELLO, ())
    assert [(f.kind, f.seq, f.resend) for _, f in agent.q] == [
        (FINISHED, 1, 0), (HELLO, 2, 0), (FINISHED, 1, 1),
    ]
    assert agent.tx_wake.is_set() and agent.last_tx == 100


def test_announce_can_keep_old_repeats_and_retreat_has_no_claims(monkeypatch):
    monkeypatch.setattr(agent_module.time, 'monotonic', lambda: 10)
    agent = make_agent(supersede=False)
    assert agent._claims_from(0) == [2, 4]
    assert agent._claims_from(1) == [4]
    agent.retreating = True
    assert agent._claims_from(0) == []
    agent.announce(ARRIVED, (1,), repeats=(1,))
    agent.announce(HELLO, ())
    assert len(agent.q) == 3
    assert all(f.retreat for _, f in agent.q)


def test_retreat_avoids_park_and_resumes_original_route():
    graph = Graph([(1, 2), (1, 3), (3, 2), (2, 4), (1, 5)], park=(5,))
    agent = make_agent(graph=graph)
    assert agent._start_retreat()
    assert agent.route == [1, 3]
    assert agent.resume == [3, 2, 4]
    assert agent.retreating and agent.retreats == 1 and agent.idx == 0


def test_retreat_fails_when_all_side_paths_are_occupied_or_parked():
    graph = Graph([(1, 2), (1, 3), (1, 5)], park=(5,))
    agent = make_agent(graph=graph)
    agent.book[1] = Frame(1, 0, ARRIVED, nodes=(3,))
    assert not agent._start_retreat()
    assert agent.route == [1, 2, 4] and not agent.retreating


def test_blocked_waits_until_threshold_then_starts_retreat(monkeypatch):
    agent = make_agent()
    agent.book[3] = Frame(3, 0, ARRIVED, nclaims=1, nodes=(9, 1))
    agent._wait = AsyncMock()
    monkeypatch.setattr(agent_module.time, 'monotonic', lambda: 100)
    asyncio.run(agent._blocked({3}))
    assert agent.blocked_since == 100
    agent._wait.assert_awaited_once_with(0.1)
    monkeypatch.setattr(agent_module.time, 'monotonic', lambda: 103)
    asyncio.run(agent._blocked({3}))
    assert agent.retreating and agent.retreats == 1


def test_drive_announces_edge_then_arrival():
    agent = make_agent(scale=0)
    agent.driver.drive = AsyncMock()
    asyncio.run(agent._drive((1, 2)))
    agent.driver.drive.assert_awaited_once_with(1, 2)
    assert (agent.cur, agent.idx) == (2, 1)
    assert [f.kind for _, f in agent.q] == [ARRIVED] * 4
    assert agent.q[0][1].nodes == (2, 4)


def test_drive_failure_does_not_advance_position():
    agent = make_agent(scale=0)
    agent.driver.drive = AsyncMock(side_effect=RuntimeError('motor failed'))
    with pytest.raises(RuntimeError, match='motor failed'):
        asyncio.run(agent._drive((1, 2)))
    assert (agent.cur, agent.idx) == (1, 0)
    assert agent.q[0][1].kind == ENTER


def test_run_completes_route_and_sends_finished():
    class RecordingRadio(Radio):
        def __init__(self):
            self.frames = []

        async def send(self, payload):
            self.frames.append(Frame.decode(payload))

    async def scenario():
        radio = RecordingRadio()
        driver = AsyncMock()
        agent = Agent(2, [1, 2, 4], Graph([(1, 2), (2, 4)]), radio, driver,
                      scale=0.001, rng=FixedRandom())
        await asyncio.wait_for(agent.run(), timeout=1)
        assert agent.done and agent.cur == 4 and agent.idx == 2
        assert driver.drive.await_args_list == [((1, 2),), ((2, 4),)]
        assert radio.cb == agent.on_rx
        assert any(frame.kind == FINISHED for frame in radio.frames)

    asyncio.run(scenario())


def test_forced_retreat_and_retreat_limit(monkeypatch):
    agent = make_agent()
    agent.blocked_since = 10
    agent.forced_after = 15
    agent._start_retreat = Mock(return_value=True)
    agent._wait = AsyncMock()
    monkeypatch.setattr(agent_module.time, 'monotonic', lambda: 25)
    asyncio.run(agent._blocked({1}))
    agent._start_retreat.assert_called_once_with()
    agent.retreats = agent_module.RETREAT_MAX
    asyncio.run(agent._blocked({1}))
    agent._start_retreat.assert_called_once_with()
    agent._wait.assert_awaited_once_with(0.1)
