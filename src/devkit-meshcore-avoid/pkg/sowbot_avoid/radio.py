"""Radios: MeshRadio (meshcore_py, companion firmware 1.15.0+) and a simulated shared channel."""
import asyncio
import time

from .frame import DATA_TYPE


class Radio:
    last_rx = 0.0
    cb = None

    def set_handler(self, cb):
        """Set the callback invoked with each received payload."""
        self.cb = cb

    async def start(self):
        """Start the radio; the base implementation does nothing."""
        pass

    async def stop(self):
        """Stop the radio; the base implementation does nothing."""
        pass

    async def send(self, payload):
        """Send a payload; subclasses must implement this operation."""
        raise NotImplementedError


class MeshRadio(Radio):
    """One MeshCore companion radio through meshcore_py."""

    def __init__(self, port, idx=1, name='sowbot', secret=None, zero_hop=True, baud=115200):
        """Configure the serial port and channel.

        Secret is a hex-encoded channel key; omit it to use an existing channel.
        Zero hop sends directly, while false permits flooding through repeaters.
        """
        self.port, self.idx, self.name, self.secret, self.zero_hop, self.baud = port, idx, name, secret, zero_hop, baud

    async def start(self):
        """Connect, optionally configure the channel, and begin fetching messages.

        Import, connection, and invalid hex secret errors propagate. RuntimeError is
        raised if the radio rejects the channel setup or has no channel at the
        configured index, because the robot would otherwise move without being heard.
        The port is disconnected again if anything after the connection fails.
        """
        from meshcore import MeshCore, EventType
        self._EventType = EventType
        key = bytes.fromhex(self.secret) if self.secret else None      # NOTE: fail on a bad key before opening the port
        self.mc = await MeshCore.create_serial(self.port, self.baud)
        try:
            if key:
                res = await self.mc.commands.set_channel(self.idx, self.name, key)
                if res.type == EventType.ERROR:
                    raise RuntimeError(f'set_channel {self.idx} failed: {res.payload}')
            res = await self.mc.commands.get_channel(self.idx)
            if res.type == EventType.ERROR:
                raise RuntimeError(f'channel {self.idx} is not available on the radio: {res.payload}')
            self.mc.subscribe(EventType.CHANNEL_DATA_RECV, self._on_event,
                              attribute_filters={'channel_idx': self.idx, 'data_type': DATA_TYPE})
            await self.mc.start_auto_message_fetching()
        except BaseException:
            await self.mc.disconnect()
            raise

    async def stop(self):
        """Disconnect the companion radio; disconnect errors propagate."""
        await self.mc.disconnect()

    async def _on_event(self, ev):
        self.last_rx = time.monotonic()
        if self.cb:
            self.cb(bytes.fromhex(ev.payload['payload']))

    async def send(self, payload):
        """Send channel data.

        An error response from the radio raises RuntimeError, so the caller sees that
        the frame was not sent. Exceptions from the radio command propagate as well.
        """
        # NOTE: path=b"" is zero-hop (direct range only); None floods through repeaters.
        res = await self.mc.commands.send_channel_data(self.idx, DATA_TYPE, payload,
                                                       path=b'' if self.zero_hop else None)
        if res.type == self._EventType.ERROR:
            raise RuntimeError(f'send_channel_data error: {res.payload}')


class LoopBus:
    """Simulated shared channel: one collision domain, overlapping frames lost, random loss, rx delay."""

    def __init__(self, rng, loss=0.0, scale=1.0, toa=0.03):
        """Configure random loss and timing for the simulated channel.

        Loss is the per-frame random loss probability; toa is airtime in seconds.
        Scale multiplies airtime and delivery delay.
        """
        self.rng, self.loss, self.k, self.toa = rng, loss, scale, toa * scale
        self.radios, self.air, self.frames, self.lost = [], [], 0, 0

    def radio(self):
        """Create and attach a radio to this bus."""
        r = LoopRadio(self)
        self.radios.append(r)
        return r

    def transmit(self, sender, payload):
        """Schedule delivery to other radios, subject to overlap and random loss.

        Requires a running asyncio event loop; RuntimeError propagates otherwise.
        """
        now = time.monotonic()
        self.air = [a for a in self.air if now - a[0] < 1.0]
        me = [now, False]
        for a in self.air:
            if now - a[0] < self.toa:
                a[1] = me[1] = True
        self.air.append(me)
        self.frames += 1
        delay = self.toa + self.rng.uniform(0.05, 0.3) * self.k
        asyncio.get_running_loop().call_later(delay, self._deliver, sender, payload, me)

    def _deliver(self, sender, payload, me):
        if me[1] or self.rng.random() < self.loss:
            self.lost += 1
            return
        for r in self.radios:
            if r is not sender:
                r.last_rx = time.monotonic()
                if r.cb:
                    r.cb(payload)


class LoopRadio(Radio):
    def __init__(self, bus):
        self.bus = bus

    async def send(self, payload):
        """Schedule a transmission on the simulated bus."""
        self.bus.transmit(self, payload)
