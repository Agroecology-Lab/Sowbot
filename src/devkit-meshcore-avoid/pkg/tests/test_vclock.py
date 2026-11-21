import asyncio
import time

import pytest

from sowbot_avoid import vclock
from sowbot_avoid.sim import simulate

ROUTES = {1: [4, 5, 6, 3, 11], 2: [2, 5, 8, 7, 12], 3: [6, 5, 4, 1, 10]}   # as in test_sim.py


def test_time_jumps_instead_of_waiting():
    async def main():
        t0 = time.monotonic()
        await asyncio.sleep(1000)
        return time.monotonic() - t0

    wall = time.perf_counter()
    assert vclock.run(main()) == pytest.approx(1000)
    assert time.perf_counter() - wall < 2


def test_monotonic_is_restored_also_after_an_error():
    real = time.monotonic

    async def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        vclock.run(boom())
    assert time.monotonic is real


def test_waiting_for_nothing_raises_instead_of_hanging():
    async def main():
        await asyncio.get_running_loop().create_future()

    with pytest.raises(RuntimeError, match="forever"):
        vclock.run(main())


@pytest.mark.parametrize("loss", [0.0, 0.2])
def test_same_seed_gives_identical_results(loss):
    a = vclock.run(simulate(ROUTES, loss, 7, scale=1.0))
    b = vclock.run(simulate(ROUTES, loss, 7, scale=1.0))
    assert a == b


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_no_loss_all_finish_and_keep_clear(seed):      # the same claim as test_sim.py, on the virtual clock
    r = vclock.run(simulate(ROUTES, 0.0, seed, scale=1.0))
    assert r["done"] and r["sep"] >= 1.0
