import asyncio
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sowbot_avoid import __main__ as cli
from sowbot_avoid.graph import Graph

KEY = '00112233445566778899aabbccddeeff'


def args(**changes):
    values = dict(map='map.json', clearance=2.0, driver='robot:Driver', serial='/dev/radio',
                  channel_idx=3, channel_name='channel', channel_secret_file=None, flood=True,
                  id=7, route='1,2,3')
    values.update(changes)
    return SimpleNamespace(**values)


def line_graph():
    return Graph([(1, 2), (2, 3)], {1: (0, 0), 2: (3, 0), 3: (6, 0)}, 2.0, park=(3,))


@pytest.fixture
def wired(monkeypatch):
    radio = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    mesh_radio = Mock(return_value=radio)
    agent = SimpleNamespace(run=AsyncMock())
    agent_factory = Mock(return_value=agent)
    driver = object()
    graph_load = Mock(return_value=line_graph())
    monkeypatch.setattr(cli.Graph, 'load', graph_load)
    monkeypatch.setattr(cli.importlib, 'import_module', Mock(return_value=SimpleNamespace(Driver=lambda: driver)))
    monkeypatch.setattr(cli, 'MeshRadio', mesh_radio)
    monkeypatch.setattr(cli, 'Agent', agent_factory)
    return SimpleNamespace(radio=radio, mesh_radio=mesh_radio, agent=agent, agent_factory=agent_factory,
                           driver=driver, graph_load=graph_load)


@pytest.mark.parametrize('failure', [False, True])
def test_run_configures_dependencies_and_always_stops_radio(wired, failure):
    if failure:
        wired.agent.run.side_effect = RuntimeError('drive failed')
        with pytest.raises(RuntimeError, match='drive failed'):
            asyncio.run(cli.run(args()))
    else:
        asyncio.run(cli.run(args()))
    wired.graph_load.assert_called_once_with('map.json', 2.0)
    wired.mesh_radio.assert_called_once_with('/dev/radio', 3, 'channel', None, False)
    positional, keyword = wired.agent_factory.call_args
    assert positional[0] == 7 and positional[1] == [1, 2, 3]
    assert positional[3:] == (wired.radio, wired.driver)
    assert set(keyword) == {'log'} and callable(keyword['log'])
    wired.radio.start.assert_awaited_once()
    wired.agent.run.assert_awaited_once()
    wired.radio.stop.assert_awaited_once()


def test_secret_is_read_from_a_file_not_the_command_line(wired, tmp_path):
    key_file = tmp_path / 'channel.key'
    key_file.write_text(KEY + '\n')
    asyncio.run(cli.run(args(channel_secret_file=str(key_file))))
    assert wired.mesh_radio.call_args.args[3] == KEY


def test_secret_can_come_from_stdin(wired, monkeypatch):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(KEY))
    asyncio.run(cli.run(args(channel_secret_file='-')))
    assert wired.mesh_radio.call_args.args[3] == KEY


@pytest.mark.parametrize('content', ['', 'not hex', KEY[:-2], KEY + '00'])
def test_bad_secret_stops_before_the_radio_is_opened(wired, tmp_path, content):
    key_file = tmp_path / 'channel.key'
    key_file.write_text(content)
    with pytest.raises(cli.MissionError, match='secret'):
        asyncio.run(cli.run(args(channel_secret_file=str(key_file))))
    wired.radio.start.assert_not_awaited()


def test_missing_secret_file_is_a_mission_error(wired, tmp_path):
    with pytest.raises(cli.MissionError, match='cannot read'):
        asyncio.run(cli.run(args(channel_secret_file=str(tmp_path / 'absent'))))
    wired.radio.start.assert_not_awaited()


@pytest.mark.parametrize('changes, message', [
    (dict(id=0), '--id'), (dict(id=256), '--id'),
    (dict(clearance=0.0), '--clearance'), (dict(clearance=-1.0), '--clearance'),
    (dict(clearance=float('nan')), '--clearance'), (dict(clearance=float('inf')), '--clearance'),
    (dict(channel_idx=-1), '--channel-idx'), (dict(channel_idx=256), '--channel-idx'),
    (dict(route=''), '--route'), (dict(route='1,x'), '--route'),
    (dict(route='1,2'), 'park node'),             # ends on a traversable node
    (dict(route='1,2,99'), 'coordinates'),        # node missing from the map
])
def test_bad_input_is_rejected_before_the_radio_is_opened(wired, changes, message):
    with pytest.raises(cli.MissionError, match=message):
        asyncio.run(cli.run(args(**changes)))
    wired.radio.start.assert_not_awaited()
    wired.radio.stop.assert_not_awaited()


def test_route_hops_are_checked_by_the_real_agent(monkeypatch, tmp_path):
    map_file = tmp_path / 'map.json'
    map_file.write_text(json.dumps({'nodes': {'1': [0, 0], '2': [3, 0], '3': [6, 0]},
                                    'edges': [[1, 2]], 'park': [3]}))
    radio = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(cli, 'MeshRadio', Mock(return_value=radio))
    monkeypatch.setattr(cli.importlib, 'import_module', Mock(return_value=SimpleNamespace(Driver=object)))
    with pytest.raises(cli.MissionError, match='not map edges'):
        asyncio.run(cli.run(args(map=str(map_file))))
    radio.start.assert_not_awaited()


def test_main_parses_defaults_and_route_arguments(monkeypatch):
    captured = []

    async def capture(parsed):
        captured.append(parsed)

    monkeypatch.setattr(cli, 'run', capture)
    monkeypatch.setattr(sys, 'argv', ['sowbot_avoid', '--serial', 'port', '--id', '2', '--map', 'map.json',
                                      '--route', '1,2', '--driver', 'robot:Driver'])
    cli.main()
    parsed = captured[0]
    assert (parsed.id, parsed.route, parsed.clearance) == (2, '1,2', 1.0)
    assert (parsed.channel_idx, parsed.channel_name, parsed.channel_secret_file) == (1, 'sowbot', None)
    assert not parsed.flood and not hasattr(parsed, 'keep_resends')


def test_main_has_no_secret_on_the_command_line(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['sowbot_avoid', '--serial', 'p', '--id', '2', '--map', 'm', '--route', '1',
                                      '--driver', 'a:B', '--channel-secret', KEY])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_main_reports_mission_errors_as_usage_errors(monkeypatch, capsys):
    async def fail(parsed):
        raise cli.MissionError('route must end at a park node')

    monkeypatch.setattr(cli, 'run', fail)
    monkeypatch.setattr(sys, 'argv', ['sowbot_avoid', '--serial', 'p', '--id', '2', '--map', 'm', '--route', '1',
                                      '--driver', 'a:B'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert 'park node' in capsys.readouterr().err


def test_main_requires_mission_arguments(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['sowbot_avoid'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert '--serial' in capsys.readouterr().err
