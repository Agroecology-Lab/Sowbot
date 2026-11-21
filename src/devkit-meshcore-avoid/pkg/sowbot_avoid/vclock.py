"""Run a coroutine on a virtual clock, so a simulation takes no real time and gives the same result every run.

The agents read time.monotonic() and sleep on the asyncio loop. VirtualLoop reports a clock that
only moves when every task is waiting: it jumps straight to the next timer. For the length of
run() it also points time.monotonic at that clock. The agents, radio and simulator need no change.

Use it for sweeps and tests only. It is not for a real radio or a real driver, which need real time.
Do not call run() from inside a running event loop, and do not run two at once: time.monotonic is
replaced process-wide while run() is active.
"""
import asyncio
import time


class VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose time() advances by jumping to the next timer instead of sleeping."""

    def __init__(self):
        super().__init__()
        self._vt = 0.0
        real_select = self._selector.select

        def select(timeout=None):
            events = real_select(0)
            if not events:
                if timeout is None:
                    raise RuntimeError("virtual clock: nothing is scheduled, the run would wait forever")
                self._vt += timeout
            return events

        self._selector.select = select

    def time(self):
        return self._vt


def run(coro):
    """Run coro to completion on a VirtualLoop and return its result.

    time.monotonic is restored afterwards, also when coro raises. RuntimeError is raised if every
    task is waiting and no timer is pending, which on a real clock would be a hang.
    """
    loop = VirtualLoop()
    real = time.monotonic
    time.monotonic = loop.time
    try:
        return loop.run_until_complete(coro)
    finally:
        time.monotonic = real
        loop.close()
