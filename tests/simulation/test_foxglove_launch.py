"""Foxglove must start on loopback and view-only unless the operator opts in."""

import pytest


def bridges(launch_file):
    """Return the declared defaults and the two bridge nodes keyed by their condition type."""
    actions = launch_file('devkit_bringup', 'foxglove_bridge.launch.py')
    defaults = {a.args[0]: a.kwargs['default_value'] for a in actions if a.kind == 'DeclareLaunchArgument'}
    nodes = {a.kwargs['condition'].kind: a for a in actions if a.kind == 'Node'}
    return defaults, nodes


def test_defaults_are_loopback_and_no_control(launch_file):
    defaults, _ = bridges(launch_file)
    assert defaults == {'foxglove_address': '127.0.0.1', 'foxglove_allow_control': 'false'}


def test_default_node_cannot_publish_or_change_parameters(launch_file):
    _, nodes = bridges(launch_file)
    capabilities = nodes['UnlessCondition'].kwargs['parameters'][0]['capabilities']
    assert 'clientPublish' not in capabilities
    assert not {'parameters', 'parametersSubscribe', 'services'} & set(capabilities)


def test_control_node_is_only_started_by_the_opt_in_argument(launch_file):
    _, nodes = bridges(launch_file)
    assert set(nodes) == {'IfCondition', 'UnlessCondition'}
    assert nodes['IfCondition'].kwargs['condition'].args[0].args == ('foxglove_allow_control',)
    assert nodes['UnlessCondition'].kwargs['condition'].args[0].args == ('foxglove_allow_control',)
    assert 'clientPublish' in nodes['IfCondition'].kwargs['parameters'][0]['capabilities']


@pytest.mark.parametrize('node_kind', ['IfCondition', 'UnlessCondition'])
def test_both_nodes_take_the_address_from_the_argument(launch_file, node_kind):
    _, nodes = bridges(launch_file)
    address = nodes[node_kind].kwargs['parameters'][0]['address']
    assert address.args[0].args == ('foxglove_address',)
    assert nodes[node_kind].kwargs['parameters'][0]['tls'] is False


@pytest.mark.parametrize('filename', ['devkit_nocams.launch.py', 'devkit_simulation.launch.py'])
def test_launch_files_no_longer_start_an_open_bridge(launch_file, filename):
    actions = launch_file('devkit_bringup', filename)
    assert not [a for a in actions if a.kind == 'Node' and a.kwargs.get('package') == 'foxglove_bridge']
    includes = [a.args[0].args[0] for a in actions if a.kind == 'IncludeLaunchDescription']
    assert any(path.endswith('foxglove_bridge.launch.py') for path in includes)
