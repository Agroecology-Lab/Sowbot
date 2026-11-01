import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sowbot_avoid.frame import DATA_TYPE
from sowbot_avoid.radio import LoopBus, MeshRadio, Radio
from sowbot_avoid import radio as radio_module


class RandomValues:
    def __init__(self, value=1):
        self.value = value

    def uniform(self, low, high):
        return low

    def random(self):
        return self.value


def test_loop_bus_delivers_to_other_radios_and_records_reception(monkeypatch):
    async def scenario():
        bus = LoopBus(RandomValues(), scale=0)
        sender, receiver, silent = bus.radio(), bus.radio(), bus.radio()
        received = []
        sender.set_handler(lambda payload: pytest.fail('sender received its own transmission'))
        receiver.set_handler(received.append)
        monkeypatch.setattr(radio_module, 'time', SimpleNamespace(monotonic=lambda: 42))
        await sender.send(b'frame')
        await asyncio.sleep(0.001)
        assert received == [b'frame']
        assert receiver.last_rx == silent.last_rx == 42
        assert (bus.frames, bus.lost) == (1, 0)

    asyncio.run(scenario())


def test_loop_bus_drops_overlapping_frames_and_random_loss(monkeypatch):
    async def scenario():
        bus = LoopBus(RandomValues(), scale=1, toa=0.01)
        sender, receiver = bus.radio(), bus.radio()
        received = []
        receiver.set_handler(received.append)
        now = [0]
        monkeypatch.setattr(radio_module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
        scheduled = []
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, 'call_later', lambda delay, callback, *args: scheduled.append((callback, args)))
        await sender.send(b'first')
        now[0] = 0.005
        await sender.send(b'second')
        for callback, args in scheduled:
            callback(*args)
        assert received == [] and (bus.frames, bus.lost) == (2, 2)
        now[0] = 2
        bus.loss = 1
        bus.rng.value = 0
        await sender.send(b'lost')
        callback, args = scheduled[-1]
        callback(*args)
        assert received == [] and (bus.frames, bus.lost) == (3, 3)
        assert len(bus.air) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize('zero_hop', [True, False])
def test_mesh_radio_configures_channel_sends_and_disconnects(monkeypatch, zero_hop, capsys):
    async def scenario():
        event_type = SimpleNamespace(CHANNEL_DATA_RECV='received', ERROR='error')
        commands = SimpleNamespace(set_channel=AsyncMock(), send_channel_data=AsyncMock(
            return_value=SimpleNamespace(type='error', payload='failed')))
        mc = SimpleNamespace(commands=commands, subscribe=Mock(), start_auto_message_fetching=AsyncMock(),
                             disconnect=AsyncMock())
        create = AsyncMock(return_value=mc)
        monkeypatch.setitem(sys.modules, 'meshcore', SimpleNamespace(
            MeshCore=SimpleNamespace(create_serial=create), EventType=event_type))
        radio = MeshRadio('/dev/radio', idx=3, name='test', secret='0102', zero_hop=zero_hop, baud=9600)
        received = []
        radio.set_handler(received.append)
        await radio.start()
        create.assert_awaited_once_with('/dev/radio', 9600)
        commands.set_channel.assert_awaited_once_with(3, 'test', b'\x01\x02')
        mc.subscribe.assert_called_once_with('received', radio._on_event,
                                             attribute_filters={'channel_idx': 3, 'data_type': DATA_TYPE})
        mc.start_auto_message_fetching.assert_awaited_once()
        await radio._on_event(SimpleNamespace(payload={'payload': '00ff'}))
        assert received == [b'\x00\xff'] and radio.last_rx > 0
        await radio.send(b'data')
        commands.send_channel_data.assert_awaited_once_with(3, DATA_TYPE, b'data',
                                                            path=b'' if zero_hop else None)
        await radio.stop()
        mc.disconnect.assert_awaited_once()

    asyncio.run(scenario())
    assert 'send_channel_data error: failed' in capsys.readouterr().err


def test_base_radio_send_is_not_implemented():
    with pytest.raises(NotImplementedError):
        asyncio.run(Radio().send(b'data'))


def test_mesh_radio_without_secret_preserves_existing_channel(monkeypatch):
    async def scenario():
        commands = SimpleNamespace(set_channel=AsyncMock())
        mc = SimpleNamespace(commands=commands, subscribe=Mock(), start_auto_message_fetching=AsyncMock())
        monkeypatch.setitem(sys.modules, 'meshcore', SimpleNamespace(
            MeshCore=SimpleNamespace(create_serial=AsyncMock(return_value=mc)),
            EventType=SimpleNamespace(CHANNEL_DATA_RECV='received')))
        radio = MeshRadio('port')
        await radio.start()
        commands.set_channel.assert_not_awaited()
        await radio._on_event(SimpleNamespace(payload={'payload': '0102'}))
        assert radio.last_rx > 0

    asyncio.run(scenario())
