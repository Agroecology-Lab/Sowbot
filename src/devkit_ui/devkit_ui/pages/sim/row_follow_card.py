# pylint: disable=consider-using-with
import os

from nicegui import run as ng_run
from nicegui import ui

from devkit_ui.pages.widgets import restore_label
from devkit_ui.process_registry import alive, kill_group, shared, spawn_logged

_MUTED = 'color:#57606a'
_OK = 'color:#1a7f37'
_ERROR = 'color:#cf222e'


class RowFollowCard(ui.card):
    """Start and stop the Sowbot row-follow stack in simulation.

    Launches neo.launch.py with the Gazebo-bridged /camera/image_raw instead of a V4L2 device,
    runs the TSM detector, and publishes /cmd_vel via crop_row_node. limbic_row_follow_node (started
    by sim_nav.launch.py) calls /row_follow/enable on this process when topo nav reaches an _IN node.
    """

    def __init__(self) -> None:
        """Build the start and stop buttons."""
        super().__init__()
        self._proc: list = shared('neo', lambda: [None])
        with self:
            ui.label('Sowbot Row Follow').classes('font-semibold mb-2')
            self._status = ui.label('').classes('text-xs font-mono').style(_MUTED)
            restore_label(self._status, self._proc[0])
            with ui.row().classes('items-center gap-2 flex-wrap'):
                ui.button('Start', on_click=self._start).props('color=positive no-caps').classes('px-4')
                ui.button('Stop', on_click=self._stop).props(
                    'color=negative outline no-caps').classes('px-4')

    def _show(self, text: str, style: str = _MUTED) -> None:
        """Show a status message in the given colour."""
        self._status.set_text(text)
        self._status.style(style)

    def _start(self) -> None:
        """Launch neo.launch.py in simulation mode unless it is already running."""
        if alive(self._proc[0]):
            self._show('already running')
            return
        try:
            self._proc[0] = spawn_logged(
                ['ros2', 'launch', 'devkit_bringup', 'neo.launch.py',
                 'use_camera:=false', 'detector:=tsm', 'image_topic:=/camera/image_raw'],
                '/tmp/neo_sim.log', env=os.environ.copy())
            self._show(f'running — pid {self._proc[0].pid} · log: /tmp/neo_sim.log', _OK)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'ERROR: {exc}', _ERROR)

    async def _stop(self) -> None:
        """Stop the row-follow process group."""
        if self._proc[0] is None:
            self._show('not running')
            return
        await ng_run.io_bound(kill_group, self._proc[0])
        self._proc[0] = None
        self._show('stopped')
