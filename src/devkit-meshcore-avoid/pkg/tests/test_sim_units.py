import asyncio

import pytest

from sowbot_avoid.sim import SimDriver, World, grid


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
