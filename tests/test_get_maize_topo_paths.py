"""Discover shared topology defaults in the host and container directory layouts."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = """
import json
import runpy
import sys
module = runpy.run_path(sys.argv[1])
import devkit_ui.topo_defaults as defaults
print(json.dumps({
    'source': str(module['_UI_SRC']),
    'loaded': defaults.__file__,
    'shared': module['ACTIONS'] == defaults.ACTIONS and module['DEFINITIONS'] == defaults.DEFINITIONS,
}))
"""


@pytest.mark.parametrize(
    ('layout', 'via_symlink'),
    [
        ('repo', False),
        ('repo', True),
        ('container', False),
        ('both', False),
        ('missing', False),
    ],
)
def test_topology_defaults_are_located_relative_to_real_script(tmp_path, layout, via_symlink):
    """Resolve both layouts without relying on the caller, PYTHONPATH, or cached UI imports."""
    workspace = tmp_path / 'workspace with spaces'
    script_dir = workspace if layout == 'container' else workspace / 'worldgen'
    script_dir.mkdir(parents=True)
    script = script_dir / 'get_maize_topo.py'
    shutil.copyfile(ROOT / 'worldgen' / script.name, script)
    roots = {
        'repo': [workspace],
        'container': [workspace],
        'both': [workspace, script_dir],
        'missing': [],
    }[layout]
    for directory in roots:
        package = directory / 'src' / 'devkit_ui' / 'devkit_ui'
        package.mkdir(parents=True)
        (package / '__init__.py').touch()
        shutil.copyfile(ROOT / 'src/devkit_ui/devkit_ui/topo_defaults.py', package / 'topo_defaults.py')
    caller = tmp_path / 'unrelated caller'
    caller.mkdir()
    if via_symlink:
        link = caller / 'topology-link.py'
        link.symlink_to(script)
        script = link

    result = subprocess.run(
        [sys.executable, '-I', '-c', PROBE, str(script)],
        cwd=caller,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    if layout == 'missing':
        assert result.returncode != 0
        assert "ModuleNotFoundError: No module named 'devkit_ui'" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    selected = (script_dir if layout == 'both' else workspace) / 'src' / 'devkit_ui'
    assert json.loads(result.stdout) == {
        'source': str(selected),
        'loaded': str(selected / 'devkit_ui' / 'topo_defaults.py'),
        'shared': True,
    }
