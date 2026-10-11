# pylint: disable=consider-using-with
import os
import signal
import subprocess
from html import escape

# pylint: disable-next=import-error
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from nicegui import run as ng_run
from nicegui import ui

from devkit_ui.pages.widgets import (
    doc_link,
    link_button,
    report_if_exited,
    request_host,
    restore_label,
)
from devkit_ui.process_registry import (
    alive,
    kill_group,
    shared,
    spawn_logged,
    start_vnc_stack,
    stop_all,
)

_GRAPHER_DIR = '/tmp/ros2grapher'
_MUTED = 'color:#57606a'
_OK = 'color:#1a7f37'
_ERROR = 'color:#cf222e'


def _status_label() -> ui.label:
    """Return an empty monospace status label."""
    return ui.label('').classes('text-xs font-mono').style(_MUTED)


def _graph_explorer(host: str) -> None:
    """Start/stop controls and links for ros2graph_explorer."""
    proc: list = shared('explorer', lambda: [None])
    lbl = _status_label()
    restore_label(lbl, proc[0])

    def start() -> None:
        if alive(proc[0]):
            lbl.set_text('already running')
            return
        try:
            proc[0] = spawn_logged(
                ['ros2', 'run', 'ros2graph_explorer', 'ros2graph_explorer'],
                '/tmp/ros2graph_explorer.log')
            lbl.set_text(f'started (pid {proc[0].pid}) - log: /tmp/ros2graph_explorer.log')
            lbl.style(_OK)
            report_if_exited(proc[0], lbl, '/tmp/ros2graph_explorer.log')
        except Exception as exc:  # pylint: disable=broad-exception-caught
            lbl.set_text(f'ERROR: {exc}')
            lbl.style(_ERROR)

    async def stop() -> None:
        await ng_run.io_bound(kill_group, proc[0])
        proc[0] = None
        lbl.set_text('stopped')
        lbl.style(_MUTED)

    ui.button('Start Graph Explorer', on_click=start).props('outline no-caps').classes('px-4')
    ui.button('Stop', on_click=stop).props('outline no-caps').classes('px-4')
    link_button(f'http://{host}:8734/', '↗ Graph Explorer')
    doc_link('https://github.com/nilseuropa/ros2graph_explorer#build--launch')


def _grapher(host: str) -> None:
    """Start/stop controls and links for ros2grapher."""
    proc: list = shared('grapher', lambda: [None])
    lbl = _status_label()
    restore_label(lbl, proc[0])

    def start() -> None:
        if alive(proc[0]):
            lbl.set_text('already running')
            return
        try:
            # ros2grapher serves its output directory over plain HTTP on all interfaces. Give it a
            # private directory holding only index.html so it cannot expose the workspace (.env etc).
            os.makedirs(_GRAPHER_DIR, exist_ok=True)
            proc[0] = spawn_logged(
                ['ros2grapher', '/workspace', '-o', f'{_GRAPHER_DIR}/index.html', '--port', '8888'],
                '/tmp/ros2grapher.log', cwd=_GRAPHER_DIR)
            lbl.set_text(
                f'started (pid {proc[0].pid}) - open http://{host}:8888 '
                f'(scan takes a few seconds) - log: /tmp/ros2grapher.log')
            lbl.style(_OK)
            report_if_exited(proc[0], lbl, '/tmp/ros2grapher.log')
        except Exception as exc:  # pylint: disable=broad-exception-caught
            lbl.set_text(f'ERROR: {exc}')
            lbl.style(_ERROR)

    async def stop() -> None:
        await ng_run.io_bound(kill_group, proc[0])
        proc[0] = None
        lbl.set_text('stopped')
        lbl.style(_MUTED)

    ui.button('Start ros2grapher', on_click=start).props('outline no-caps').classes('px-4')
    ui.button('Stop', on_click=stop).props('outline no-caps').classes('px-4')
    link_button(f'http://{host}:8888/', '↗ ros2grapher')
    doc_link('https://github.com/Supull/ros2grapher')


def _rviz(host: str) -> None:
    """Start/stop controls and the noVNC link for RViz."""
    proc: list = shared('rviz', lambda: [None])
    daemons: list = shared('rviz_daemons', list)
    lbl = _status_label()
    restore_label(lbl, proc[0])

    async def start() -> None:
        if alive(proc[0]):
            lbl.set_text('already running')
            return
        try:
            # topo_nav's RViz config has MarkerArray displays pre-wired to
            # /topological_map_visualisation et al. Falls back to no config if topo_nav isn't installed.
            try:
                rviz_cfg = os.path.join(
                    get_package_share_directory('topological_navigation'),
                    'rviz', 'topological_navigation.rviz',
                )
            except PackageNotFoundError:
                rviz_cfg = None
            await ng_run.io_bound(start_vnc_stack, ':98', 5901, 6081, daemons)
            args = ['ros2', 'run', 'rviz2', 'rviz2']
            if rviz_cfg is not None:
                args += ['-d', rviz_cfg]
            proc[0] = spawn_logged(args, '/tmp/rviz.log', env={**os.environ, 'DISPLAY': ':98'})
            suffix = '' if rviz_cfg else ' (no topo config found)'
            lbl.set_text(f'started (pid {proc[0].pid}){suffix} - log: /tmp/rviz.log')
            lbl.style(_OK)
            report_if_exited(proc[0], lbl, '/tmp/rviz.log')
        except Exception as exc:  # pylint: disable=broad-exception-caught
            lbl.set_text(f'ERROR: {exc}')
            lbl.style(_ERROR)

    async def stop() -> None:
        await ng_run.io_bound(kill_group, proc[0])
        proc[0] = None
        await ng_run.io_bound(stop_all, daemons)
        lbl.set_text('stopped')
        lbl.style(_MUTED)

    ui.button('Launch RViz', on_click=start).props('outline no-caps').classes('px-4')
    ui.button('Stop RViz', on_click=stop).props('outline no-caps').classes('px-4')
    link_button(f'http://{host}:6081/vnc.html', '↗ RViz (noVNC)')


def _medkit_controls() -> None:
    """Start/stop controls and links for the ros2_medkit gateway.

    Self-contained on purpose: test_medkit_gateway executes this body with UI and subprocess doubles.
    """
    _medkit_proc: list = [None]
    _medkit_stopping = False
    _medkit_lbl = ui.label('').classes('text-xs font-mono').style('color:#57606a')
    def _start_medkit():
        """Launch Medkit on loopback unless this tab's process is running.

        Display process creation errors in the status label. A successful
        start reports the launch PID without checking gateway readiness.
        """
        if _medkit_stopping:
            return
        if _medkit_proc[0] is not None and _medkit_proc[0].poll() is None:
            _medkit_lbl.set_text('already running')
            return
        try:
            _medkit_proc[0] = subprocess.Popen(
                ['ros2', 'launch', 'ros2_medkit_gateway', 'bringup.launch.py',
                 'server_host:=127.0.0.1'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _medkit_lbl.set_text(f'started (pid {_medkit_proc[0].pid})')
            _medkit_lbl.style('color:#1a7f37')
        except Exception as exc:
            _medkit_lbl.set_text(f'ERROR: {exc}')
            _medkit_lbl.style('color:#cf222e')
    async def _stop_medkit():
        """Reap the launch process before releasing its handle, off the UI loop."""
        nonlocal _medkit_stopping
        if _medkit_stopping:
            return
        _medkit_stopping = True
        try:
            if _medkit_proc[0] is not None:
                _medkit_lbl.set_text('stopping')
                await ng_run.io_bound(_shutdown_medkit, _medkit_proc[0])
                _medkit_proc[0] = None
            _medkit_lbl.set_text('stopped')
            _medkit_lbl.style('color:#57606a')
        except (OSError, subprocess.TimeoutExpired) as exc:
            _medkit_lbl.set_text(f'ERROR: {exc}')
            _medkit_lbl.style('color:#cf222e')
        finally:
            _medkit_stopping = False

    def _shutdown_medkit(proc):
        """Allow ROS launch to stop its children before escalating on timeout."""
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    ui.button('Start Medkit Gateway', on_click=_start_medkit).props(
        'outline no-caps').classes('px-4')
    ui.button('Stop Medkit Gateway', on_click=_stop_medkit).props(
        'outline no-caps').classes('px-4')
    _medkit_host = ui.context.client.request.url.hostname or 'localhost'
    if ':' in _medkit_host:
        _medkit_host = f'[{_medkit_host}]'
    _medkit_host = escape(_medkit_host, quote=True)
    ui.html(
        f'<a href="http://{_medkit_host}:8080/" target="_blank" '
        'style="font-size:13px;color:var(--blue);text-decoration:none;'
        'padding:6px 12px;border:1px solid var(--blue);border-radius:4px;'
        'font-family:\'Courier New\',monospace;">'
        f'↗ Gateway ({_medkit_host}:8080)</a>'
    )
    ui.html(
        '<div style="font-size:11px;color:var(--txt-muted);'
        'font-family:\'Courier New\',monospace;line-height:1.5;">'
        'SOVD web UI runs on the host, not in this container. On host:<br>'
        '<code>docker run -p 3000:80 ghcr.io/selfpatch/sovd_web_ui:latest</code><br>'
        f'Then open <a href="http://{_medkit_host}:3000/" target="_blank" '
        f'style="color:var(--blue);text-decoration:none;">{_medkit_host}:3000</a> '
        f'and set its gateway URL to <code>http://{_medkit_host}:8080</code>.<br>'
        'The gateway starts on loopback for host-local use. Remote access requires '
        'authentication, TLS, and restricted network access.'
        '</div>'
    )


class ToolsCard(ui.card):
    """Launch and stop the developer tools: Graph Explorer, ros2grapher, RViz and the Medkit gateway."""

    def __init__(self) -> None:
        """Build one control group per tool, separated by rules."""
        super().__init__()
        with self:
            ui.label('Tools').classes('font-semibold mb-2')
            with ui.row().classes('items-center gap-3 flex-wrap'):
                host = request_host()
                _graph_explorer(host)
                ui.separator().classes('w-full my-1')
                _grapher(host)
                ui.separator().classes('w-full my-1')
                _rviz(host)
                ui.separator().classes('w-full my-1')
                _medkit_controls()
