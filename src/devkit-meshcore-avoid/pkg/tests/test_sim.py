import asyncio

import pytest

from sowbot_avoid.sim import simulate

ROUTES = {1: [4, 5, 6, 3, 11], 2: [2, 5, 8, 7, 12], 3: [6, 5, 4, 1, 10]}   # meet at node 5; 1 and 3 head-on


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_no_loss_all_finish_and_keep_clear(seed):
    r = asyncio.run(simulate(ROUTES, 0.0, seed))
    assert r["done"] and r["sep"] >= 1.0


@pytest.mark.xfail(reason="seed 2 at 20% loss: two retreats pick the same waypoint, one robot livelocks")
def test_loss_20_seed_2():
    r = asyncio.run(simulate(ROUTES, 0.2, 2))
    assert r["done"] and r["sep"] >= 1.0
