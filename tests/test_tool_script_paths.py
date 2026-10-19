"""Exercise shell path resolution with offline command doubles and no hardware access."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMAND_DOUBLE = """
import json
import os
import sys
from pathlib import Path
command = Path(sys.argv[0]).name
with open(os.environ['COMMAND_LOG'], 'a') as log:
    log.write(json.dumps({'command': command, 'args': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')
if command == 'git' and sys.argv[1] in ('rev-parse', 'ls-remote'):
    sys.exit(1)
"""


@pytest.fixture
def shell_repo(tmp_path, monkeypatch):
    """Create a temporary repo, isolated command doubles, and an external caller directory."""
    repo = tmp_path / 'repo with spaces'
    tools = repo / 'tools'
    tools.mkdir(parents=True)
    for name in ('docker.sh', 'git.sh', 'espinfo.sh', 'flash-lizard.sh'):
        shutil.copy(ROOT / 'tools' / name, tools / name)
    (tools / 'fixusb.py').touch()
    commands = tmp_path / 'commands'
    commands.mkdir()
    for name in ('docker-compose', 'git', 'python3', 'sudo'):
        double = commands / name
        double.write_text(f'#!{sys.executable}\n{COMMAND_DOUBLE}')
        double.chmod(0o755)
    # NOTE: Only these real commands are available; all external actions use doubles.
    for name in ('dirname', 'echo', 'grep', 'xargs'):
        (commands / name).symlink_to(shutil.which(name))
    log = tmp_path / 'commands.jsonl'
    monkeypatch.setenv('PATH', str(commands))
    monkeypatch.setenv('COMMAND_LOG', str(log))
    for name in ('BASH_ENV', 'ENV', 'CDPATH'):
        monkeypatch.delenv(name, raising=False)
    caller = tmp_path / 'unrelated caller'
    caller.mkdir()
    return repo, caller, log


@pytest.mark.parametrize(
    ('operation', 'expected'),
    [
        ('up', ['-f', 'docker/docker-compose.yml', 'up', '-d', 'runtime']),
        ('restart', ['-f', 'docker/docker-compose.yml', 'restart', 'runtime']),
        ('ps', ['-f', 'docker/docker-compose.yml', 'ps', 'runtime']),
    ],
)
def test_compose_resolves_configuration_from_repo(shell_repo, operation, expected):
    """Relative Compose configuration must resolve at the repo root for every invocation."""
    repo, caller, log = shell_repo

    result = run_script(repo, caller, 'docker.sh', operation, 'runtime')

    assert result.returncode == 0, result.stderr
    assert read_calls(log) == [{'command': 'docker-compose', 'args': expected, 'cwd': str(repo)}]


def test_git_stages_repo_root_from_external_cwd(shell_repo):
    """The helper's git add . must cover the whole repo; every Git action is a double."""
    repo, caller, log = shell_repo

    result = run_script(repo, caller, 'git.sh', '-test-branch', 'test message')

    assert result.returncode == 0, result.stderr
    calls = read_calls(log)
    assert calls
    assert all(call['command'] == 'git' and call['cwd'] == str(repo) for call in calls)
    assert ['add', '.'] in [call['args'] for call in calls]


@pytest.mark.parametrize('name', ['espinfo.sh', 'flash-lizard.sh'])
def test_hardware_tools_find_sibling_fixusb_from_external_cwd(shell_repo, name):
    """Resolve the sibling helper before stopping at missing local prerequisites."""
    repo, caller, log = shell_repo
    (caller / '.env').write_text('MCU_PORT=/dev/caller-mcu\n')

    result = run_script(repo, caller, name)

    assert result.returncode == 1
    assert read_calls(log) == [
        {
            'command': 'python3',
            'args': [str(repo / 'tools' / 'fixusb.py')],
            'cwd': str(repo if name == 'flash-lizard.sh' else caller),
        }
    ]
    expected = 'Virtual environment not found' if name == 'espinfo.sh' else '.env file missing'
    assert expected in result.stdout


@pytest.mark.parametrize('name', ['espinfo.sh', 'flash-lizard.sh'])
def test_missing_sibling_fixusb_does_not_run_caller_script(shell_repo, name):
    """An unrelated fixusb.py in the caller directory must never be selected."""
    repo, caller, log = shell_repo
    (repo / 'tools' / 'fixusb.py').unlink()
    (caller / 'fixusb.py').touch()
    (repo / 'fixusb.py').touch()

    result = run_script(repo, caller, name)

    assert result.returncode == 1
    assert read_calls(log) == []
    if name == 'flash-lizard.sh':
        assert 'Warning: tools/fixusb.py not found.' in result.stdout


@pytest.mark.parametrize('audit_present', [True, False])
def test_flasher_finds_optional_sibling_audit(shell_repo, audit_present):
    """Finish the mocked flash sequence and select espinfo.sh beside the flasher."""
    repo, caller, log = shell_repo
    firmware = repo / 'firmware'
    for name in ('bootloader/bootloader.bin', 'partition_table/partition-table.bin', 'lizard.bin'):
        binary = firmware / 'build' / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.touch()
    (repo / '.env').write_text('MCU_PORT=/dev/test-mcu\nLIZARD_DIR=firmware\n')
    (caller / '.env').write_text('MCU_PORT=/dev/wrong-mcu\nLIZARD_DIR=wrong-firmware\n')
    audit = repo / 'tools' / 'espinfo.sh'
    if audit_present:
        audit.write_text(f'#!{sys.executable}\n{COMMAND_DOUBLE}')
    else:
        audit.unlink()
    # NOTE: The audit at the repo root must not be used, including when the sibling is absent.
    local_audit = repo / 'espinfo.sh'
    local_audit.write_text(f'#!{sys.executable}\n{COMMAND_DOUBLE}\nsys.exit(93)\n')
    local_audit.chmod(0o755)

    result = run_script(repo, caller, 'flash-lizard.sh')

    assert result.returncode == 0, result.stderr
    calls = read_calls(log)
    flash = next(call for call in calls if call['args'][:2] == ['-m', 'esptool'])
    assert flash['args'][flash['args'].index('--port') + 1] == '/dev/test-mcu'
    assert all(call['cwd'] == str(repo) for call in calls)
    audits = [call for call in calls if call['command'] == 'espinfo.sh']
    assert audits == ([{'command': 'espinfo.sh', 'args': [], 'cwd': str(repo)}] if audit_present else [])


def run_script(repo, caller, name, *arguments):
    """Run a tool with Bash from the caller directory and capture its output with a timeout."""
    return subprocess.run(
        ['/bin/bash', str(repo / 'tools' / name), *arguments],
        cwd=caller,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def read_calls(log):
    """Return recorded command calls, or an empty list if no log was created."""
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
