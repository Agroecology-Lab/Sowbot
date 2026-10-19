"""Repository paths must work when manage.py is called from another directory."""

import importlib.util
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def managed_repo(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('manage_paths', ROOT / 'manage.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.signal, 'signal', Mock())
    manager = module.DevkitManager()
    manager.root_dir = tmp_path / 'repo with spaces'
    manager.root_dir.mkdir()
    caller = tmp_path / 'caller'
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.delenv('FORCE_SIM', raising=False)
    monkeypatch.setenv('USER', 'test-operator')
    monkeypatch.delenv('SUDO_USER', raising=False)
    usb = tmp_path / 'usb-devices'
    monkeypatch.setattr(module, 'Path', lambda path: usb if str(path) == '/sys/bus/usb/devices' else Path(path))
    monkeypatch.setattr(module.shutil, 'which', lambda name: None)
    monkeypatch.setattr(module.time, 'sleep', Mock())
    for name, result in (
        ('_find_ublox_interfaces', []),
        ('_gpu_render_flags', []),
        ('_cyclonedds_uri', 'test-dds'),
        ('_unmeasured_lever_arm', False),
        ('_unconfirmed_settings', []),
        ('_usb_reset_f9p', None),
        ('_cdc_acm_bind', None),
        ('_cdc_acm_unbind', None),
    ):
        monkeypatch.setattr(manager, name, Mock(return_value=result))
    runner = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(module.subprocess, 'run', runner)
    return manager, runner, usb


@pytest.mark.parametrize('options', [{}, {'sim': True}, {'full_clean': True}, {'pull_external': True}])
@pytest.mark.parametrize('returncode', [0, 1])
def test_build_uses_repository_context_from_another_directory(managed_repo, monkeypatch, options, returncode):
    """Every build mode resolves Dockerfile and context at the repository root, even on failure."""
    manager, runner, _ = managed_repo
    caller = Path.cwd()
    monkeypatch.setenv('BUILD_PATH_TEST', 'preserved')
    runner.return_value = subprocess.CompletedProcess([], returncode)

    if returncode:
        with pytest.raises(SystemExit) as raised:
            manager.build(**options)
        assert raised.value.code == 1
    else:
        manager.build(**options)

    runner.assert_called_once()
    command = runner.call_args.args[0]
    assert command[:2] == ['docker', 'build']
    assert command[command.index('-f') + 1] == 'docker/Dockerfile'
    assert '.' in command
    assert runner.call_args.kwargs == {
        'cwd': manager.root_dir,
        'check': False,
        'env': {**os.environ, 'DOCKER_BUILDKIT': '1'},
    }
    assert Path.cwd() == caller


def test_fixusb_runs_from_repo_and_writes_env_there(managed_repo):
    """The relocated helper must not write .env into the caller's directory."""
    manager, runner, usb = managed_repo
    usb.mkdir()
    helper = manager.root_dir / 'tools' / 'fixusb.py'
    helper.parent.mkdir()
    helper.touch()
    caller = Path.cwd()

    def run(command, **kwargs):
        if command[:2] == ['sudo', 'python3']:
            destination = Path(kwargs.get('cwd', Path.cwd())) / '.env'
            destination.write_text('GPS_PORT_ROVER=virtual\nMCU_PORT=virtual\n')
        return subprocess.CompletedProcess(command, 0)

    runner.side_effect = run
    manager.run(['--sim'])

    assert runner.call_args_list[0].args[0] == ['sudo', 'python3', str(helper)]
    assert runner.call_args_list[0].kwargs == {'cwd': manager.root_dir, 'check': True}
    assert (manager.root_dir / '.env').read_text() == 'GPS_PORT_ROVER=virtual\nMCU_PORT=virtual\n'
    assert not (caller / '.env').exists()
    assert Path.cwd() == caller
    assert any(call.args[0][:2] == ['docker', 'run'] for call in runner.call_args_list)


@pytest.mark.parametrize('scenario', ['no_usb', 'missing_helper', 'legacy_helper'])
def test_fixusb_is_skipped_without_usb_or_relocated_helper(managed_repo, scenario):
    """A missing helper or USB tree must not trigger sudo or the old root script."""
    manager, runner, usb = managed_repo
    if scenario != 'no_usb':
        usb.mkdir()
    helper = manager.root_dir / ('tools/fixusb.py' if scenario == 'no_usb' else 'fixusb.py')
    if scenario != 'missing_helper':
        helper.parent.mkdir(exist_ok=True)
        helper.touch()

    manager.run(['--sim'])

    assert all(call.args[0][0] != 'sudo' for call in runner.call_args_list)
    assert runner.call_args.args[0][:2] == ['docker', 'run']


def test_fixusb_failure_stops_before_starting_container(managed_repo):
    """Failure of the relocated hardware setup must propagate to the caller."""
    manager, runner, usb = managed_repo
    usb.mkdir()
    helper = manager.root_dir / 'tools' / 'fixusb.py'
    helper.parent.mkdir()
    helper.touch()
    command = ['sudo', 'python3', str(helper)]
    runner.side_effect = subprocess.CalledProcessError(2, command)

    with pytest.raises(subprocess.CalledProcessError) as raised:
        manager.run(['--sim'])

    assert raised.value.returncode == 2
    runner.assert_called_once_with(command, cwd=manager.root_dir, check=True)


@pytest.mark.parametrize('simulation', [True, False])
def test_worldgen_mounts_keep_container_entry_points(managed_repo, simulation):
    """Moved host files keep the paths used inside both simulation and hardware containers."""
    manager, runner, _ = managed_repo
    scripts = ('get_maize_topo.py', 'topo_to_forest3d.py', 'worldgen.sh')
    worldgen = manager.root_dir / 'worldgen'
    worldgen.mkdir()
    for name in scripts:
        (worldgen / name).touch()
    if not simulation:
        (manager.root_dir / '.env').write_text(
            'GPS_PORT_ROVER=/dev/test-missing-gps\nMCU_PORT=virtual\nSOWBOT_UI_PASSWORD=test-password\n'
        )

    manager.run([])

    runner.assert_called_once()
    command = runner.call_args.args[0]
    mounts = [command[index + 1] for index, value in enumerate(command) if value == '-v']
    script_mounts = [mount for mount in mounts if mount.split(':')[1] in {f'/workspace/{name}' for name in scripts}]
    assert set(script_mounts) == {f'{worldgen / name}:/workspace/{name}:ro' for name in scripts}
    assert len(script_mounts) == len(scripts)
    for mount in script_mounts:
        assert Path(mount.split(':')[0]).is_file()
    assert ('python3 /workspace/get_maize_topo.py' in command[-1]) is simulation
    assert ('bash /workspace/worldgen.sh' in command[-1]) is simulation
