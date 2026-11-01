import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sowbot_avoid import __main__ as cli


def args(**changes):
    values = dict(map='map.json', clearance=2.0, driver='robot:Driver', serial='/dev/radio',
                  channel_idx=3, channel_name='channel', channel_secret='abcd', flood=True,
                  id=7, route='1,2,3', keep_resends=True)
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('failure', [False, True])
def test_run_configures_dependencies_and_always_stops_radio(monkeypatch, failure):
    graph, driver = object(), object()
    graph_load = Mock(return_value=graph)
    radio = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    mesh_radio = Mock(return_value=radio)
    agent = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError('drive failed') if failure else None))
    agent_factory = Mock(return_value=agent)
    monkeypatch.setattr(cli.Graph, 'load', graph_load)
    monkeypatch.setattr(cli.importlib, 'import_module', Mock(return_value=SimpleNamespace(Driver=lambda: driver)))
    monkeypatch.setattr(cli, 'MeshRadio', mesh_radio)
    monkeypatch.setattr(cli, 'Agent', agent_factory)
    if failure:
        with pytest.raises(RuntimeError, match='drive failed'):
            asyncio.run(cli.run(args()))
    else:
        asyncio.run(cli.run(args()))
    graph_load.assert_called_once_with('map.json', 2.0)
    mesh_radio.assert_called_once_with('/dev/radio', 3, 'channel', 'abcd', False)
    positional, keyword = agent_factory.call_args
    assert positional == (7, [1, 2, 3], graph, radio, driver)
    assert keyword['supersede'] is False and callable(keyword['log'])
    radio.start.assert_awaited_once()
    agent.run.assert_awaited_once()
    radio.stop.assert_awaited_once()


def test_main_parses_defaults_and_route_arguments(monkeypatch):
    import sys

    captured = []

    async def capture(parsed):
        captured.append(parsed)

    monkeypatch.setattr(cli, 'run', capture)
    monkeypatch.setattr(sys, 'argv', ['sowbot_avoid', '--serial', 'port', '--id', '2', '--map', 'map.json',
                                      '--route', '1,2', '--driver', 'robot:Driver'])
    cli.main()
    parsed = captured[0]
    assert (parsed.id, parsed.route, parsed.clearance) == (2, '1,2', 1.0)
    assert (parsed.channel_idx, parsed.channel_name, parsed.channel_secret) == (1, 'sowbot', None)
    assert not parsed.flood and not parsed.keep_resends


def test_main_requires_mission_arguments(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, 'argv', ['sowbot_avoid'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert '--serial' in capsys.readouterr().err
