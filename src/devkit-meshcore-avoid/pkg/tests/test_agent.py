import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest

from sowbot_avoid import ARRIVED, BEACON, ENTER, FINISHED, HELLO, Agent, Frame, Graph
from sowbot_avoid import agent as agent_module
from sowbot_avoid.radio import Radio


class FixedRandom:
    def uniform(self, low, high):
        return (low + high) / 2


class RecordingRadio(Radio):
    def __init__(self, fail=False):
        self.frames, self.fail = [], fail

    async def send(self, payload):
        if self.fail:
            raise OSError('radio down')
        self.frames.append(Frame.decode(payload))


def make_agent(route=(1, 2, 4), graph=None, radio=None, **kwargs):
    graph = graph or Graph([(1, 2), (1, 3), (2, 3), (2, 4)])
    return Agent(2, route, graph, radio or Radio(), AsyncMock(), rng=FixedRandom(), **kwargs)


def test_receive_rejects_invalid_own_duplicate_and_old_frames():
    agent = make_agent()
    agent.on_rx(b'bad')
    agent.on_rx(Frame(2, 1, HELLO).encode())
    assert agent.book == {} and not agent.wake.is_set()
    agent.on_rx(Frame(1, 255, HELLO).encode())
    assert agent.book[1].seq == 255 and agent.wake.is_set()
    agent.wake.clear()
    for seq in (255, 254, 127):  # duplicate, backwards, half-range ambiguity
        agent.on_rx(Frame(1, seq, FINISHED).encode())
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
    agent = make_agent(route=(1, 2), graph=graph)
    agent.book[1] = Frame(1, 0, ARRIVED, nodes=(3,))
    assert not agent._start_retreat()
    assert agent.route == [1, 2] and not agent.retreating


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


def test_drive_sends_enter_before_driving_then_announces_arrival():
    async def scenario():
        radio = RecordingRadio()
        agent = make_agent(scale=0, radio=radio)
        seen = []
        agent.driver.drive = AsyncMock(side_effect=lambda a, b: seen.append([f.kind for f in radio.frames]))
        sender = asyncio.create_task(agent._sender())
        assert await agent._drive((1, 2))
        await asyncio.sleep(0.01)
        sender.cancel()
        agent.driver.drive.assert_awaited_once_with(1, 2)
        assert seen[0] and seen[0][0] == ENTER
        assert (agent.cur, agent.idx) == (2, 1)
        originals = [f for f in radio.frames if f.resend == 0]
        assert [f.kind for f in originals] == [ENTER, ARRIVED]
        assert originals[1].nodes == (2, 4)

    asyncio.run(scenario())


def test_drive_failure_does_not_advance_position():
    async def scenario():
        radio = RecordingRadio()
        agent = make_agent(scale=0, radio=radio)
        agent.driver.drive = AsyncMock(side_effect=RuntimeError('motor failed'))
        sender = asyncio.create_task(agent._sender())
        with pytest.raises(RuntimeError, match='motor failed'):
            await agent._drive((1, 2))
        sender.cancel()
        assert (agent.cur, agent.idx) == (1, 0)
        assert radio.frames[0].kind == ENTER

    asyncio.run(scenario())


def test_drive_does_not_move_when_enter_is_not_sent():
    async def scenario():
        agent = make_agent(scale=0, radio=RecordingRadio(fail=True))
        sender = asyncio.create_task(agent._sender())
        assert not await agent._drive((1, 2))
        sender.cancel()
        agent.driver.drive.assert_not_awaited()
        assert (agent.cur, agent.idx) == (1, 0)

    asyncio.run(scenario())


def test_drive_does_not_move_when_a_peer_claims_the_edge_while_enter_is_queued():
    async def scenario():
        radio = RecordingRadio()
        agent = make_agent(scale=0, radio=radio)
        announce = agent.announce

        def announce_then_peer_holds_edge(kind, *args, **kwargs):
            delivery = announce(kind, *args, **kwargs)
            if kind == ENTER:
                agent.on_rx(Frame(3, 1, ENTER, nodes=(2, 5)).encode())      # arrives before our ENTER is sent
            return delivery

        agent.announce = announce_then_peer_holds_edge
        sender = asyncio.create_task(agent._sender())
        assert not await agent._drive((1, 2))
        await asyncio.sleep(0.01)
        sender.cancel()
        agent.driver.drive.assert_not_awaited()
        assert (agent.cur, agent.idx) == (1, 0)
        assert [f.kind for f in radio.frames if f.resend == 0] == [ENTER, ARRIVED]     # hold withdrawn

    asyncio.run(scenario())


def test_old_frame_with_a_reused_sequence_number_cannot_settle_the_new_delivery():
    agent = make_agent(supersede=False)
    first = agent.announce(ENTER, (1, 2))
    old_frame = agent.q[0][1]
    agent.seq = old_frame.seq - 1               # NOTE: the sequence number comes round again while the old frame is pending
    second = agent.announce(ENTER, (1, 2))
    new_frame = agent.q[-1][1]
    assert new_frame.seq == old_frame.seq and first.event.is_set() and not first.ok
    agent._settle(old_frame, True)
    assert not second.event.is_set()
    agent._settle(new_frame, True)
    assert second.event.is_set() and second.ok


def test_sender_transmits_even_if_the_channel_never_goes_quiet():
    class BusyRadio(RecordingRadio):
        @property
        def last_rx(self):
            return time.monotonic()

    async def scenario():
        radio = BusyRadio()
        agent = make_agent(scale=0.01, radio=radio)
        sender = asyncio.create_task(agent._sender())
        delivery = agent.announce(HELLO, ())
        await asyncio.wait_for(delivery.event.wait(), timeout=2)
        sender.cancel()
        assert delivery.ok and [f.kind for f in radio.frames] == [HELLO]

    asyncio.run(scenario())


def test_superseded_frame_settles_delivery_as_not_sent():
    agent = make_agent()
    enter = agent.announce(ENTER, (1, 2))
    assert not enter.event.is_set()
    agent.announce(ARRIVED, (1,))
    assert enter.event.is_set() and not enter.ok


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
    asyncio.run(agent._blocked({3}))
    agent._start_retreat.assert_called_once_with()
    agent.retreats = agent_module.RETREAT_MAX
    asyncio.run(agent._blocked({3}))
    agent._start_retreat.assert_called_once_with()
    agent._wait.assert_awaited_once_with(0.1)


def test_only_the_lower_id_retreats(monkeypatch):
    agent = make_agent()                         # id 2
    agent.blocked_since, agent.forced_after = 10, 15
    agent._start_retreat, agent._wait = Mock(return_value=True), AsyncMock()
    monkeypatch.setattr(agent_module.time, 'monotonic', lambda: 100)
    asyncio.run(agent._blocked({1}))             # a lower id blocks us: wait for it
    asyncio.run(agent._blocked({1, 3}))          # any lower id among the blockers is enough to wait
    agent._start_retreat.assert_not_called()
    assert agent._wait.await_count == 2


def test_only_the_lowest_id_retreats(monkeypatch):
    agent = make_agent()                      # robot 2
    agent.blocked_since, agent.forced_after = 10, 15
    agent._start_retreat, agent._wait = Mock(return_value=True), AsyncMock()
    monkeypatch.setattr(agent_module.time, 'monotonic', lambda: 25)
    asyncio.run(agent._blocked({1}))          # blocked by a lower id: wait for it
    asyncio.run(agent._blocked({1, 3}))       # one blocker is lower: still wait
    agent._start_retreat.assert_not_called()
    assert agent._wait.await_count == 2


@pytest.mark.parametrize(('delta', 'accepted'), [(1, True), (127, True), (128, False), (255, False)])
def test_receive_sequence_window_boundaries(delta, accepted):
    agent = make_agent()
    original = Frame(1, 200, HELLO)
    agent.on_rx(original.encode())
    agent.wake.clear()
    incoming = Frame(1, (200 + delta) % 256, FINISHED)
    agent.on_rx(incoming.encode())
    assert agent.book[1] == (incoming if accepted else original)
    assert agent.wake.is_set() is accepted


def test_sender_continues_after_radio_error_and_logs_it():
    async def scenario():
        agent = make_agent(supersede=False)
        error = RuntimeError('radio unavailable')
        sent = asyncio.Event()

        async def send(payload):
            if not sent.is_set():
                sent.set()
                raise error
            received.append(Frame.decode(payload))
            delivered.set()

        received = []
        delivered = asyncio.Event()
        agent.radio.send = send
        agent.log = Mock()
        agent.announce(HELLO, ())
        agent.announce(FINISHED, ())
        sender = asyncio.create_task(agent._sender())
        try:
            await asyncio.wait_for(delivered.wait(), timeout=1)
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)    # collect the cancellation
        assert [frame.kind for frame in received] == [FINISHED]
        agent.log.assert_called_once_with('send failed', error)
        assert agent.sent == 2

    asyncio.run(scenario())


def test_mission_waits_for_initial_node_to_clear():
    agent = make_agent(route=(1,), scale=0)
    agent.blocked_by = Mock(side_effect=[{3}, set()])

    async def wait(_delay):
        assert [frame.kind for _, frame in agent.q] == [HELLO]

    agent._wait = AsyncMock(side_effect=wait)
    agent.announce = Mock(wraps=agent.announce)
    asyncio.run(agent._mission())
    assert [call.args for call in agent.blocked_by.call_args_list] == [((1, 1),), ((1, 1),)]
    agent._wait.assert_awaited_once_with(0.1)
    assert [call.args[0] for call in agent.announce.call_args_list] == [HELLO, ARRIVED, FINISHED]


def test_mission_completes_retreat_and_resumes_route():
    async def scenario():
        graph = Graph([(1, 2), (1, 3), (3, 2), (2, 4)])
        agent = make_agent(graph=graph, scale=0, radio=RecordingRadio())
        agent.driver.drive = AsyncMock()
        agent.log = Mock()
        assert agent._start_retreat()
        sender = asyncio.create_task(agent._sender())
        await asyncio.wait_for(agent._mission(), timeout=5)
        sender.cancel()
        return agent

    agent = asyncio.run(scenario())
    assert agent.driver.drive.await_args_list == [((1, 3),), ((3, 2),), ((2, 4),)]
    assert (agent.cur, agent.idx, agent.retreating, agent.resume) == (4, 2, False, None)
    agent.log.assert_any_call('retreat done, resuming')


def test_drive_sends_beacon_while_motion_is_in_progress(monkeypatch):
    async def scenario():
        agent = make_agent(scale=0.001, radio=RecordingRadio())
        release = asyncio.Event()

        async def drive(_a, _b):
            await release.wait()

        announce = agent.announce

        def record(kind, *args, **kwargs):
            delivery = announce(kind, *args, **kwargs)
            if kind == BEACON:
                release.set()
            return delivery

        agent.driver.drive = drive
        agent.announce = Mock(side_effect=record)
        sender = asyncio.create_task(agent._sender())
        await asyncio.wait_for(agent._drive((1, 2)), timeout=1)
        sender.cancel()
        calls = agent.announce.call_args_list
        assert [call.args[0] for call in calls] == [ENTER, BEACON, ARRIVED]
        assert calls[1].args[1:3] == ([1, 2, 4], 1)
        assert (agent.cur, agent.idx) == (2, 1)

    monkeypatch.setattr(agent_module, 'BEACON_EVERY', 0)
    asyncio.run(scenario())
