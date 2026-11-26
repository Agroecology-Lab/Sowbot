"""Socket-free virtual-clock loop for Pyodide. Same behaviour as vclock.VirtualLoop: time jumps to the next timer."""
from asyncio import base_events


class _Sel:
    def __init__(self, loop):
        self.loop = loop

    def select(self, timeout=None):
        if timeout is None:
            raise RuntimeError("virtual clock: nothing is scheduled, the run would wait forever")
        self.loop._vt += timeout
        return []

    def close(self):
        pass


class WebVirtualLoop(base_events.BaseEventLoop):
    def __init__(self):
        super().__init__()
        self._vt, self._selector = 0.0, _Sel(self)

    def time(self):
        return self._vt

    def _process_events(self, events):
        pass

    def _write_to_self(self):
        pass
