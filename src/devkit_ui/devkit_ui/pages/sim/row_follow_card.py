from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import run as ng_run
from nicegui import ui

from devkit_ui.pages.widgets import STATUS_COLOURS, show_status

if TYPE_CHECKING:
    from devkit_ui.view_models.sim_process_view_model import SimProcessViewModel


class RowFollowCard(ui.card):
    """Start and stop the Sowbot row-follow stack in simulation.

    Launches neo.launch.py with the Gazebo-bridged /camera/image_raw instead of a V4L2 device,
    runs the TSM detector, and publishes /cmd_vel via crop_row_node. limbic_row_follow_node (started
    by sim_nav.launch.py) calls /row_follow/enable on this process when topo nav reaches an _IN node.
    """

    def __init__(self, process_vm: SimProcessViewModel) -> None:
        """Build the start and stop buttons.

        Parameters:
            process_vm (SimProcessViewModel): Starts, stops and reports on the row-follow process.
        """
        super().__init__()
        self._vm = process_vm
        with self:
            ui.label('Sowbot Row Follow').classes('font-semibold mb-2')
            self._status = ui.label('').classes('text-xs font-mono').style(
                f'color:{STATUS_COLOURS["muted"]}')
            show_status(self._status, process_vm.row_follow_status())
            with ui.row().classes('items-center gap-2 flex-wrap'):
                ui.button('Start', on_click=self._start).props('color=positive no-caps').classes('px-4')
                ui.button('Stop', on_click=self._stop).props(
                    'color=negative outline no-caps').classes('px-4')

    async def _start(self) -> None:
        """Launch neo.launch.py in simulation mode unless it is already running."""
        show_status(self._status, await ng_run.io_bound(self._vm.start_row_follow))

    async def _stop(self) -> None:
        """Stop the row-follow process group."""
        show_status(self._status, await ng_run.io_bound(self._vm.stop_row_follow))
