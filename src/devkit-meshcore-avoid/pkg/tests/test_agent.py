import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from sowbot_avoid import Agent, Frame, Graph
from sowbot_avoid import agent as agent_module
from sowbot_avoid.radio import Radio


class FixedRandom:
    def uniform(self, low, high):
        return (low + high) / 2


class RecordingRadio(Radio):
    def __init__(self, fail=0):
        self.frames, self.fail = [], fail

    async def send(self, payload):
        if self.fail:
            self.fail -= 1
            raise OSError('radio down')
        self.frames.append(Frame.decode(payload))


def make_agent(route=(1, 2, 4), graph=None, radio=None, rid=2, **kwargs):
    graph = graph or Graph([(1, 2), (1, 3), (2, 3), (2, 4)])
    return Agent(rid, route, graph, radio or RecordingRadio(), AsyncMock(), rng=FixedRandom(), **kwargs)


def hear(agent, rid, *nodes, seq=0, age=0.0):
    agent.book[rid] = Frame(rid, seq, nodes)
    agent.seen[rid] = time.monotonic() - age


# ---- construction ------------------------------------------------------------------------------
@pytest.mark.parametrize('kwargs, message', [
    (dict(rid=0), 'robot id'), (dict(rid=256), 'robot id'),
    (dict(route=()), 'empty'), (dict(route=(1, 65536)), 'outside 0-65535'),
    (dict(route=(1, 4)), 'not map edges'),
])
def test_bad_configuration_is_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        make_agent(**kwargs)


# ---- receive -----------------------------------------------------------------------------------
def test_receive_keeps_the_latest_valid_frame_per_robot():
    agent = make_agent()
    agent.on_rx(b'bad')
    agent.on_rx(Frame(2, 1, (1,)).encode())                    # our own id
    assert agent.book == {} and not agent.wake.is_set()
    agent.on_rx(Frame(1, 5, (3,)).encode())
    assert agent.book[1].nodes == (3,) and agent.wake.is_set()
    agent.wake.clear()
    agent.on_rx(Frame(1, 5, (4,)).encode())                    # same seq: ignored
    assert agent.book[1].nodes == (3,) and not agent.wake.is_set()
    agent.on_rx(Frame(1, 6, (4,)).encode())
    assert agent.book[1].nodes == (4,) and agent.wake.is_set()


# ---- blocking ----------------------------------------------------------------------------------
def test_a_hold_always_blocks():
    agent = make_agent()
    hear(agent, 5, 2)                                          # higher id, brand new, standing on node 2
    assert agent.blocked_by((1, 2)) == {5}
    assert agent.blocked_by((1, 3)) == set()


def test_lower_id_claims_win_and_higher_id_claims_wait_until_committed():
    agent = make_agent()
    hear(agent, 1, 9, 2)                                       # lower id claims 9->2
    hear(agent, 3, 9, 3)                                       # higher id claims 9->3, just announced
    assert agent.blocked_by((1, 2)) == {1}
    assert agent.blocked_by((1, 3)) == set()
    agent.seen[3] -= agent_module.ARBITRATE + 0.1              # now older than the arbitration window
    assert agent.blocked_by((1, 3)) == {3}


def test_claims_that_include_our_own_node_are_ignored():
    agent = make_agent()
    hear(agent, 1, 3, 1)                                       # lower id claims 3->1, we stand on 1
    assert agent.blocked_by((1, 2)) == set()


def test_conflict_uses_clearance_when_there_are_coordinates():
    graph = Graph([(1, 2), (2, 3)], {1: (0, 0), 2: (3, 0), 3: (3, 0.5)}, clearance=1.0)
    agent = make_agent(route=(1, 2), graph=graph)
    hear(agent, 5, 3)
    assert agent.blocked_by((1, 2)) == {5}                     # node 3 is 0.5 m from the edge end


# ---- send --------------------------------------------------------------------------------------
def test_announce_sends_current_node_and_next_two():
    agent = make_agent(route=(1, 2, 4, 2, 1))
    agent.seq = 255
    asyncio.run(agent._announce())
    frame = agent.radio.frames[0]
    assert frame == Frame(2, 0, (1, 2, 4))                      # seq wrapped
    agent.idx, agent.cur = 3, 2
    asyncio.run(agent._announce())
    assert agent.radio.frames[1] == Frame(2, 1, (2, 1))
    agent.idx, agent.cur = 4, 1
    asyncio.run(agent._announce())
    assert agent.radio.frames[2] == Frame(2, 2, (1,))           # last node: hold only


def test_announce_retries_until_the_radio_takes_it():
    radio = RecordingRadio(fail=2)
    agent = make_agent(radio=radio, scale=0.01)
    logged = []
    agent.log = lambda *a: logged.append(a)
    asyncio.run(agent._announce())
    assert len(radio.frames) == 1 and len(logged) == 2


def test_announce_waits_while_the_channel_is_busy(monkeypatch):
    radio = RecordingRadio()
    agent = make_agent(radio=radio, scale=0.01)
    radio.last_rx = time.monotonic()
    slept = []

    async def fake_sleep(t):
        slept.append(t)
        radio.last_rx = 0.0

    monkeypatch.setattr(agent_module.asyncio, 'sleep', fake_sleep)
    asyncio.run(agent._announce())
    assert len(slept) == 1 and len(radio.frames) == 1


# ---- mission -----------------------------------------------------------------------------------
def test_run_drives_every_edge_and_sends_one_frame_per_node():
    radio = RecordingRadio()
    agent = make_agent(route=(1, 2, 4), radio=radio, scale=0.001)
    asyncio.run(agent.run())
    assert agent.done and agent.cur == 4
    assert [c.args for c in agent.driver.drive.call_args_list] == [(1, 2), (2, 4)]
    assert [f.nodes for f in radio.frames] == [(1, 2, 4), (2, 4), (4,)]


def test_run_waits_for_a_hold_to_clear():
    async def scenario():
        agent = make_agent(route=(1, 2), scale=0.01, rid=7)    # higher id: waits, never side-steps
        hear(agent, 5, 2)

        async def release():
            await asyncio.sleep(0.3)
            agent.on_rx(Frame(5, 1, (4,)).encode())

        task = asyncio.create_task(release())
        t0 = time.monotonic()
        await agent.run()
        await task
        return agent, time.monotonic() - t0

    agent, took = asyncio.run(scenario())
    assert agent.done and took >= 0.3
    agent.driver.drive.assert_awaited_once_with(1, 2)


def test_driver_failure_propagates_and_does_not_advance():
    agent = make_agent(route=(1, 2), scale=0.001)
    agent.driver.drive.side_effect = RuntimeError('wheel stuck')
    with pytest.raises(RuntimeError, match='wheel stuck'):
        asyncio.run(agent.run())
    assert agent.cur == 1 and agent.idx == 0 and not agent.done


# ---- deadlock ----------------------------------------------------------------------------------
def test_only_the_lower_id_gives_way_and_only_after_the_wait(monkeypatch):
    agent = make_agent()
    now = [100.0]
    monkeypatch.setattr(agent_module.time, 'monotonic', lambda: now[0])
    assert not agent._stuck({3})                               # clock starts
    now[0] += agent_module.STUCK - 0.1
    assert not agent._stuck({3})
    now[0] += 0.2
    assert agent._stuck({3})
    assert not agent._stuck({1})                               # a lower id blocks: wait, do not give way
    assert not agent._stuck({1, 3})


def test_side_step_limit(monkeypatch):
    agent = make_agent()
    agent.sidesteps = agent_module.SIDESTEP_MAX
    agent.blocked_since = 0
    assert not agent._stuck({3})
