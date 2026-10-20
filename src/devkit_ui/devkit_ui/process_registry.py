"""Registry of long-running tool processes, shared by every browser session.

Tool processes (RViz, Gazebo, ...) are registered here rather than in a page closure so that
reloading or opening a second browser tab cannot orphan a running tool or spawn a duplicate.
UI-neutral: no NiceGUI and no ROS imports.
"""
import os
import signal
import subprocess
import time
from collections.abc import Callable

_TOOLS: dict = {}


def shared(name: str, factory: Callable):
    """Return the shared tool entry, storing the factory result if absent.

    The factory is called on every lookup, even when an entry already exists.
    """
    return _TOOLS.setdefault(name, factory())


def alive(proc) -> bool:
    """Return whether a process handle exists and is still running."""
    return proc is not None and proc.poll() is None


def spawn_logged(cmd: list, log: str, **kwargs):
    """Start cmd in its own session and return its process handle.

    Truncate log and redirect both output streams there.
    Forward kwargs to Popen; file-opening and process-creation errors propagate.
    """
    with open(log, 'w', encoding='utf-8') as fh:
        return subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,  # pylint: disable=consider-using-with
                                start_new_session=True, **kwargs)


def spawn_quiet(cmd: list, **kwargs):
    """Start cmd in its own session with both output streams discarded.

    Forward kwargs to Popen; process-creation errors propagate.
    The new session keeps shutdown_tools' kill_group from signalling the UI's own process group.
    """
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,  # pylint: disable=consider-using-with
                            start_new_session=True, **kwargs)


def kill_group(proc, grace: float = 5.0) -> None:
    """Request process-group termination, escalating if the leader outlives grace seconds.

    Do nothing for a missing or already-exited process handle.
    Send SIGTERM, then SIGKILL if waiting for the leader times out.
    Ignore a vanished group and a timeout in the final two-second wait;
    other OS errors propagate, and descendant exit is not verified.
    Blocking: call off the UI loop.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def interrupt_stop(proc, interrupt_wait: float, terminate_wait: float, kill_wait: float) -> None:
    """Stop a launch leader gently, escalating only after each wait times out.

    Send SIGINT to the leader only, so ROS launch can stop its children, and wait interrupt_wait
    seconds. Then terminate and wait terminate_wait, then kill and wait kill_wait.
    OS errors and a timeout after kill propagate. Blocking: call off the UI loop.
    """
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=interrupt_wait)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=terminate_wait)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=kill_wait)


def stop_all(procs: list) -> None:
    """Stop the listed process groups and clear the list; blocks while waiting."""
    for proc in procs:
        kill_group(proc, grace=3.0)
    procs.clear()


def ensure_xvfb(display: str, daemons: list) -> None:
    """Start Xvfb unless a live one owns the display; clear stale locks."""
    num = display.lstrip(':')
    lock = f'/tmp/.X{num}-lock'
    if os.path.exists(lock):
        if subprocess.run(['pgrep', '-f', f'[X]vfb {display}'],
                          capture_output=True, check=False).returncode == 0:
            return
        for path in (lock, f'/tmp/.X11-unix/X{num}'):
            try:
                os.remove(path)
            except OSError:
                pass
    daemons.append(spawn_logged(
        ['Xvfb', display, '-screen', '0', '1920x1080x24', '-nolisten', 'tcp'],
        f'/tmp/xvfb{num}.log'))
    time.sleep(0.5)


def start_vnc_stack(display: str, vnc_port: int, web_port: int, daemons: list) -> None:
    """Xvfb + x11vnc (loopback only) + noVNC websockify. Blocking."""
    stop_all(daemons)
    ensure_xvfb(display, daemons)
    daemons.append(spawn_logged(
        ['x11vnc', '-display', display, '-nopw', '-forever', '-shared', '-quiet',
         '-localhost', '-rfbport', str(vnc_port)], f'/tmp/x11vnc-{vnc_port}.log'))
    daemons.append(spawn_logged(
        ['websockify', '--web', '/usr/share/novnc', str(web_port), f'localhost:{vnc_port}'],
        f'/tmp/websockify-{web_port}.log'))
    time.sleep(0.5)


def shutdown_tools() -> None:
    """Stop registered tool process groups during application shutdown."""
    for entry in _TOOLS.values():
        for proc in (entry if isinstance(entry, list) else [entry]):
            if isinstance(proc, subprocess.Popen):
                kill_group(proc, grace=3.0)
