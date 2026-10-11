"""Process and request helpers shared by the System and Sim pages.

Tool processes (RViz, Gazebo, ...) are registered here rather than in a page closure so that
reloading or opening a second browser tab cannot orphan a running tool or spawn a duplicate.
No ROS imports.
"""
import os
import signal
import subprocess
import time
from collections.abc import Callable
from html import escape

from nicegui import ui

_TOOLS: dict = {}


def shared(name: str, factory: Callable):
    """Return the shared tool entry, storing the factory result if absent."""
    return _TOOLS.setdefault(name, factory())


def alive(proc) -> bool:
    """Return whether a process handle exists and is still running."""
    return proc is not None and proc.poll() is None


def spawn_logged(cmd: list, log: str, **kwargs):
    """Start cmd in its own session with output going to a log file."""
    with open(log, 'w', encoding='utf-8') as fh:
        return subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,  # pylint: disable=consider-using-with
                                start_new_session=True, **kwargs)


def kill_group(proc, grace: float = 5.0) -> None:
    """SIGTERM then SIGKILL a process group. Blocking: call off the UI loop."""
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


def restore_label(lbl, proc) -> None:
    """Show a running process in a status label after a page reload."""
    if alive(proc):
        lbl.set_text(f'running (pid {proc.pid})')
        lbl.style('color:#1a7f37')


def report_if_exited(proc, lbl, log: str) -> None:
    """Two seconds after start, say so if the process already died."""
    def check() -> None:
        if proc.poll() is not None:
            lbl.set_text(f'exited ({proc.returncode}) - see {log}')
            lbl.style('color:#cf222e')
    ui.timer(2.0, check, once=True)


def shutdown_tools() -> None:
    """Stop registered tool process groups during application shutdown."""
    for entry in _TOOLS.values():
        for proc in (entry if isinstance(entry, list) else [entry]):
            if isinstance(proc, subprocess.Popen):
                kill_group(proc, grace=3.0)


def request_host() -> str:
    """Return the browser's request hostname, bracketed for IPv6 and HTML-escaped for links."""
    host = ui.context.client.request.url.hostname or 'localhost'
    if ':' in host:
        host = f'[{host}]'
    return escape(host, quote=True)


def link_button(url: str, label: str) -> None:
    """Render an outlined external link styled like the tool buttons."""
    ui.html(
        f'<a href="{url}" target="_blank" '
        'style="font-size:13px;color:var(--blue);text-decoration:none;'
        'padding:6px 12px;border:1px solid var(--blue);border-radius:4px;'
        f'font-family:\'Courier New\',monospace;">{label}</a>'
    )


def doc_link(url: str, label: str = '📄 Documentation') -> None:
    """Render a small muted documentation link."""
    ui.html(
        f'<a href="{url}" target="_blank" '
        'style="font-size:11px;color:var(--txt-muted);text-decoration:none;'
        f'font-family:\'Courier New\',monospace;">{label}</a>'
    )
