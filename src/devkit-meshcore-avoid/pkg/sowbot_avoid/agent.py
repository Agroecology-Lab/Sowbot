"""The rules: notebook, hold/claim checks, repeats, beacons, deadlock retreats (Sowbot issue #202)."""
import asyncio
import contextlib
import random
import time
from dataclasses import replace

from .frame import ARRIVED, BEACON, ENTER, FINISHED, HELLO, Frame


# NOTE: Timers from issue #202, in seconds. Everything is multiplied by --timescale (tests only).
LISTEN = 1.5
ENTER_REPEATS = (0.7)
ARRIVED_REPEATS = (1.0)
BEACON_EVERY = 5.0
TAIL = 2.0
BACKOFF = (0.12, 0.48)
LBT_MAX = 2.0           # NOTE: longest listen-before-talk wait; a busy channel must not hold a frame back forever
STUCK_RETREAT = 3.0
FORCED_RETREAT = (10.0, 20.0)
RETREAT_MAX = 10
RETREAT_ARBITRATE = 1.5  # NOTE: retreat hops wait this long for rival retreats; the lower id keeps its hop
ARBITRATE = 1.5          # NOTE: after the first claims go out, wait for rival claims before moving (covers the first resend)


class Delivery:
    """Outcome of one queued frame: ok once it was sent, not ok if it was dropped or failed to send."""

    def __init__(self):
        self.event, self.ok = asyncio.Event(), False

    def settle(self, ok):
        """Record the outcome and wake waiters; only the first call counts."""
        if not self.event.is_set():
            self.ok = ok
            self.event.set()


class Agent:
    def __init__(self, rid, route, graph, radio, driver, scale=1.0, rng=None, log=None, supersede=True):
        """Configure an agent with a nonempty route of node IDs.

        Scale multiplies protocol delays; supersede drops pending non-FINISHED
        transmissions when a new frame is queued. ValueError is raised for a robot ID
        outside 1-255, an empty route, a node outside 0-65535, or a hop that is not a
        map edge: such a robot would drive without its peers seeing it.
        """
        route = list(route)
        if not 1 <= rid <= 255:
            raise ValueError(f'robot id {rid} outside 1-255')
        if not route:
            raise ValueError('route is empty')
        bad = [n for n in route if not 0 <= n <= 65535]
        if bad:
            raise ValueError(f'route nodes outside 0-65535: {bad}')
        hops = [(a, b) for a, b in zip(route, route[1:]) if b not in graph.adj.get(a, ())]
        if hops:
            raise ValueError(f'route hops that are not map edges: {hops}')
        self.rid, self.route, self.g, self.radio, self.driver = rid, route, graph, radio, driver
        self.k, self.rng, self.supersede = scale, rng or random.Random(), supersede
        self.log = log or (lambda *a: None)
        self.book = {}                     # NOTE: robot id -> latest Frame (the "notebook")
        self.seen = {}                     # NOTE: robot id -> when its latest frame arrived
        # NOTE: Random start: a rebooted robot must not look like an old, repeated frame to its peers.
        self.seq, self.idx, self.cur = int(self.rng.uniform(0, 256)) & 255, 0, route[0]
        self.q, self.last_tx, self.sent, self.sending = [], 0.0, 0, False
        self.deliveries = {}               # NOTE: seq -> (original frame, its Delivery)
        self.wake, self.tx_wake = asyncio.Event(), asyncio.Event()
        self.retreating, self.retreats, self.resume = False, 0, None
        self.blocked_since = None
        self.forced_after = self.rng.uniform(*FORCED_RETREAT) * self.k
        self.done = False

    # ---- receive -------------------------------------------------------------------------------
    def on_rx(self, payload):
        """Record a valid, newer frame from another robot and wake the mission.

        Invalid, own, duplicate, and older frames are ignored; sequence numbers
        are compared modulo 256 using a forward window of 1 through 127.
        A HELLO with a different sequence number is accepted whatever the window,
        because the sender has restarted and its old entry is stale.
        """
        f = Frame.decode(payload)
        if f is None or f.rid == self.rid:
            return
        old = self.book.get(f.rid)
        if old is not None and not 1 <= (f.seq - old.seq) & 255 <= 127:
            if not (f.kind == HELLO and f.seq != old.seq):      # NOTE: a new HELLO is a reboot: start over
                return                      # NOTE: repeat of a frame we have, or an older one
        self.book[f.rid] = f
        self.seen[f.rid] = time.monotonic()
        self.wake.set()

    # ---- rules ---------------------------------------------------------------------------------
    def blocked_by(self, edge):
        """Return IDs blocking an edge by a hold or a qualifying claim.

        Claims from lower IDs block, or claims from any ID while retreating;
        claims containing the current node and FINISHED frames are ignored.
        Conflicts are determined by the graph.
        """
        out = set()
        for rid, f in self.book.items():
            if f.kind == FINISHED:
                continue
            h = f.hold()
            if h and self.g.conflict(edge, h):
                out.add(rid)
                continue
            if rid < self.rid or self.retreating:           # NOTE: a retreating robot gives way to everyone
                for c in f.claims():
                    if self.cur in c:                       # NOTE: claim on the spot we stand on: ignore
                        continue
                    if self.g.conflict(edge, c):
                        out.add(rid)
                        break
        return out

    def mutual(self, j):
        """Return whether robot j's first claim conflicts with our current node."""
        f = self.book.get(j)
        cl = f.claims() if f else ()
        return bool(cl) and self.g.conflict(cl[0], (self.cur, self.cur))

    # ---- send ----------------------------------------------------------------------------------
    def announce(self, kind, nodes, nclaims=0, repeats=()):
        """Queue a new frame and optional retransmissions; return the Delivery of the original.

        Repeats are delays in seconds before scaling and jitter. Pending
        non-FINISHED frames are discarded first when supersede is enabled;
        their deliveries settle as not ok.
        """
        self.seq = (self.seq + 1) & 255
        f = Frame(self.rid, self.seq, kind, 0, nclaims, self.retreating, tuple(nodes))
        now = time.monotonic()
        if self.supersede:      # NOTE: a newer full-state frame makes older pending resends pointless
            for _, x in self.q:
                if x.kind != FINISHED:
                    self._settle(x, False)
            self.q = [x for x in self.q if x[1].kind == FINISHED]
        if old := self.deliveries.pop(f.seq, None):         # NOTE: sequence number reused while the old frame is pending
            old[1].settle(False)
        delivery = Delivery()
        self.deliveries[f.seq] = (f, delivery)
        self.q.append((now, f))
        for i, d in enumerate(repeats, 1):
            self.q.append((now + d * self.k * self.rng.uniform(0.7, 1.3), replace(f, resend=i)))
        self.q.sort(key=lambda x: x[0])
        self.last_tx = now
        self.tx_wake.set()
        return delivery

    def _settle(self, f, ok):
        entry = self.deliveries.get(f.seq)
        if f.resend == 0 and entry and entry[0] is f:       # NOTE: only the frame the Delivery was made for may settle it
            del self.deliveries[f.seq]
            entry[1].settle(ok)

    async def _sender(self):
        while True:
            if not self.q:
                self.tx_wake.clear()
                await self.tx_wake.wait()
                continue
            wait = self.q[0][0] - time.monotonic()
            if wait > 0:
                self.tx_wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):     # NOTE: timeout means the next frame is due
                    await asyncio.wait_for(self.tx_wake.wait(), wait)
                continue
            _, f = self.q.pop(0)
            self.sending = True             # NOTE: run() must not stop while this frame is still in flight
            try:
                deadline = time.monotonic() + LBT_MAX * self.k
                while time.monotonic() - self.radio.last_rx < 0.1 * self.k and time.monotonic() < deadline:     # NOTE: crude listen-before-talk, bounded
                    await asyncio.sleep(self.rng.uniform(*BACKOFF) * self.k)
                try:
                    await self.radio.send(f.encode())
                except Exception as e:                                          # NOTE: radio errors must not kill us
                    self.log('send failed', e)
                    self._settle(f, False)
                else:
                    self._settle(f, True)
                self.last_tx = time.monotonic()
                self.sent += 1
            finally:
                self.sending = False

    # ---- route helpers -------------------------------------------------------------------------
    def _claims_from(self, i):
        """Return up to two destination node IDs after index i, or none while retreating."""
        if self.retreating:
            return []
        return [self.route[j + 1] for j in range(i, min(i + 2, len(self.route) - 1))]

    async def _wait(self, t):
        self.wake.clear()
        with contextlib.suppress(asyncio.TimeoutError):             # NOTE: timeout just means nothing arrived
            await asyncio.wait_for(self.wake.wait(), t * self.k)

    # ---- mission -------------------------------------------------------------------------------
    async def run(self):
        """Follow the route and drain queued announcements, then mark the agent done.

        Driver errors propagate; the sender task is cancelled on exit. Radio
        send errors are caught by the sender and passed to the log callback.
        """
        self.radio.set_handler(self.on_rx)
        sender = asyncio.create_task(self._sender())
        try:
            await self._mission()
            while self.q or self.sending:       # NOTE: include the frame the sender has already taken off the queue
                await asyncio.sleep(0.05 * self.k)
            self.done = True
        finally:
            sender.cancel()

    async def _mission(self):
        await asyncio.sleep(self.rng.uniform(0, 1.0) * self.k)   # NOTE: start-up jitter: robots rarely boot in the same instant
        self.announce(HELLO, ())
        await asyncio.sleep(LISTEN * self.k)
        while self.blocked_by((self.cur, self.cur)):
            await self._wait(0.1)
        nx = self._claims_from(0)
        self.announce(ARRIVED, [self.cur] + nx, len(nx), ARRIVED_REPEATS)
        await asyncio.sleep(ARBITRATE * self.k)         # NOTE: best effort: let rival first claims arrive before moving
        while True:
            if self.retreating and self.idx >= len(self.route) - 1:
                self.route, self.idx, self.retreating, self.resume = self.resume, 0, False, None
                self.log('retreat done, resuming')
                nx = self._claims_from(0)
                self.announce(ARRIVED, [self.cur] + nx, len(nx), ARRIVED_REPEATS)
            if self.idx >= len(self.route) - 1:
                break
            edge = (self.cur, self.route[self.idx + 1])
            blockers = self.blocked_by(edge)
            if blockers:
                await self._blocked(blockers)
                continue
            self.blocked_since = None
            if not await self._drive(edge):
                await self._wait(0.1)
        self.announce(FINISHED, (), 0, ARRIVED_REPEATS)
        self.log('finished')

    async def _drive(self, edge):
        """Drive one edge; return False if the robot did not move, True once arrived.

        The robot stays put if the ENTER frame was dropped or failed to send, or if a retreat hop
        was given up to a rival. Driver errors propagate and leave the position unchanged.
        """
        a, b = edge
        nx = self._claims_from(self.idx + 1)
        nodes = [a, b] + nx
        delivery = self.announce(ENTER, nodes, len(nx), ENTER_REPEATS)
        await delivery.event.wait()         # NOTE: peers must hold the edge before we move onto it
        if not delivery.ok:
            self.log(f'ENTER {a}->{b} not sent, not driving')
            return False
        if not self.retreating and self.blocked_by(edge):       # NOTE: retreat hops are settled by the tie-break below
            self.log(f'edge {a}->{b} blocked while ENTER was queued, not driving')
            self.announce(ARRIVED, [a], 0, ARRIVED_REPEATS)     # NOTE: withdraw the hold we just announced
            return False
        if self.retreating:
            # NOTE: Two robots can pick the same side-step point at once and neither has heard the other yet.
            # NOTE: Hold the hop for a moment; if a rival retreat conflicts, the lower id keeps it, the other backs off.
            started = time.monotonic()
            await asyncio.sleep(RETREAT_ARBITRATE * self.k)
            if self._retreat_lost(edge, started):
                self.log(f'retreat hop {a}->{b} yielded')
                self.announce(ARRIVED, [a], 0, ARRIVED_REPEATS)
                return False
        task = asyncio.create_task(self.driver.drive(a, b))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.05 * self.k)
                if not task.done() and time.monotonic() - self.last_tx >= BEACON_EVERY * self.k:
                    self.announce(BEACON, nodes, len(nx))
            task.result()                                   # NOTE: re-raises a driver failure
        finally:
            if not task.done():
                task.cancel()                               # NOTE: never leave the robot driving unsupervised
        self.cur, self.idx = b, self.idx + 1
        await asyncio.sleep(TAIL * self.k)                  # NOTE: keep the edge held while the tail clears
        nx = self._claims_from(self.idx)
        self.announce(ARRIVED, [self.cur] + nx, len(nx), ARRIVED_REPEATS)
        return True

    def _retreat_lost(self, edge, started):
        """Return whether a hold in the notebook still conflicts with a retreat hop we announced at started.

        A higher-id rival that announced its own retreat hop within the arbitration window
        does not count: it loses the tie and yields to us.
        """
        for rid, f in self.book.items():
            h = f.hold()
            if f.kind == FINISHED or not h or not self.g.conflict(edge, h):
                continue
            tie = f.retreat and f.kind == ENTER and rid > self.rid
            if tie and self.seen.get(rid, 0.0) >= started - RETREAT_ARBITRATE * self.k:
                continue
            return True
        return False

    # ---- deadlocks -----------------------------------------------------------------------------
    async def _blocked(self, blockers):
        now = time.monotonic()
        if self.blocked_since is None:
            self.blocked_since = now
        stuck = now - self.blocked_since
        # NOTE: Only the lower id retreats: a robot blocked by a lower id waits for it, so two never retreat at once.
        if not self.retreating and self.retreats < RETREAT_MAX and all(j > self.rid for j in blockers):
            pair = any(self.mutual(j) for j in blockers) and stuck >= STUCK_RETREAT * self.k
            if (pair or stuck >= self.forced_after) and self._start_retreat():
                return
        await self._wait(0.1)

    def _start_retreat(self):
        holds = [f.hold() for f in self.book.values() if f.kind != FINISHED and f.hold()]
        claims = [c for f in self.book.values() if f.kind != FINISHED for c in f.claims()]
        free_edge = lambda u, v: not any(self.g.conflict((u, v), h) for h in holds)
        free_node = lambda n: not any(self.g.conflict((n, n), h) for h in holds + claims)
        nxt = self.route[self.idx + 1]
        reach, queue = {self.cur}, [self.cur]
        for u in queue:                                     # NOTE: nearest free side-step point
            for v in sorted(self.g.adj.get(u, ())):
                if v not in reach and v not in self.g.park and free_edge(u, v):
                    reach.add(v)
                    queue.append(v)
                    if v != nxt and free_node(v):
                        wp = self.g.path(self.cur, v, free_edge)
                        back = self.g.path(v, nxt)
                        if wp and back:
                            self.resume = back + self.route[self.idx + 2:]
                            self.route, self.idx, self.retreating = wp, 0, True
                            self.retreats += 1
                            self.blocked_since = None
                            self.forced_after = self.rng.uniform(*FORCED_RETREAT) * self.k
                            self.log(f'retreat {self.retreats}: via {wp}')
                            return True
        return False
