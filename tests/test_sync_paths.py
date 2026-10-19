"""The relocated sync command must select the repository before constructing folders."""

import runpy
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'sync.py'


@pytest.fixture
def sync_double(tmp_path, monkeypatch):
    caller = tmp_path / 'outside repo'
    caller.mkdir()
    monkeypatch.chdir(caller)
    livesync = types.ModuleType('livesync')
    livesync.Folder = Mock(side_effect=lambda *args: (Path.cwd(), args))
    livesync.sync = Mock(side_effect=lambda *args: Path.cwd())
    monkeypatch.setitem(sys.modules, 'livesync', livesync)
    return livesync


@pytest.mark.parametrize('via_symlink', [False, True])
def test_sync_selects_repo_before_resolving_source(sync_double, tmp_path, monkeypatch, via_symlink):
    """Relative source '.' must resolve to the repo even through a symlink outside it."""
    entry = SCRIPT
    if via_symlink:
        entry = tmp_path / 'sync-link.py'
        entry.symlink_to(SCRIPT)
    monkeypatch.setattr(sys, 'argv', [str(entry), 'test-robot.local'])

    runpy.run_path(str(entry), run_name='__main__')

    root = SCRIPT.parent.parent
    sync_double.Folder.assert_called_once_with('.', 'test-robot.local:~/feldfreund_devkit_ros')
    sync_double.sync.assert_called_once_with((root, ('.', 'test-robot.local:~/feldfreund_devkit_ros')))
    assert Path.cwd() == root


@pytest.mark.parametrize('arguments', [[], ['robot.local', '--unknown-option']])
def test_invalid_sync_arguments_leave_cwd_and_files_untouched(sync_double, monkeypatch, arguments):
    """Argument errors must occur before changing directory or beginning a sync."""
    caller = Path.cwd()
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), *arguments])

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(SCRIPT), run_name='__main__')

    assert raised.value.code == 2
    assert Path.cwd() == caller
    sync_double.Folder.assert_not_called()
    sync_double.sync.assert_not_called()
