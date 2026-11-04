import asyncio

import pytest

from sowbot_avoid.sim import SimDriver, World, grid
from sowbot_avoid import sim as sim_module


def test_world_tracks_minimum_separation_over_time():
    world = World()
    world.set(1, (0, 0))
    world.set(2, (3, 4))
    assert world.min_sep == 5 and world.pair == (2, 1)
    world.set(2, (0, 2))
    world.set(2, (10, 10))
    assert world.min_sep == 2 and world.pair == (2, 1)


def test_grid_has_bidirectional_edges_and_docks():
    graph = grid()
    assert graph.xy[5] == (2.5, 2.5)
    assert graph.park == {10, 11, 12}
    assert graph.path(10, 11) == [10, 1, 2, 3, 11]


def test_driver_places_and_reaches_destination():
    graph = grid()
    world = World()
    driver = SimDriver(world, 1, graph, scale=0)
    driver.place(1)
    assert world.pos[1] == graph.xy[1]
    asyncio.run(driver.drive(1, 2))
    assert world.pos[1] == graph.xy[2]
    with pytest.raises(KeyError):
        driver.place(999)


def test_driver_rejects_missing_coordinates_and_zero_speed():
    driver = SimDriver(World(), 1, grid(), speed=0)
    with pytest.raises(KeyError):
        asyncio.run(driver.drive(1, 999))
    with pytest.raises(ZeroDivisionError):
        asyncio.run(driver.drive(1, 2))


def test_simulate_timeout_returns_partial_results_and_cancels_unfinished_agent(monkeypatch):
    agents = []

    class FakeAgent:
        def __init__(self, rid, route, graph, radio, driver, scale, rng):
            self.rid, self.done, self.sidesteps = rid, False, rid
            self.cancelled = False
            agents.append(self)

        async def run(self):
            if self.rid == 1:
                self.done = True
                return
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    monkeypatch.setattr(sim_module, 'Agent', FakeAgent)
    result = asyncio.run(sim_module.simulate({1: [1], 2: [2]}, loss=0, seed=0, scale=0.01, timeout=2))
    assert result['done'] is False
    assert result['sep'] == 2.5
    assert result['sidesteps'] == 3
    assert result['frames'] == result['lost'] == 0
    assert agents[0].done and agents[1].cancelled
