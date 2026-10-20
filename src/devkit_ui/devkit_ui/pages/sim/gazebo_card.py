from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import run as ng_run
from nicegui import ui

from devkit_ui.pages.sim.plant_placement_card import PlantSettings
from devkit_ui.pages.widgets import STATUS_COLOURS, link_button, request_host, show_status
from devkit_ui.view_models.sim_view_model import SimViewModel, WorldRebuildError

if TYPE_CHECKING:
    from devkit_ui.view_models.sim_process_view_model import GazeboMode, SimProcessViewModel

_MUTED = f'color:{STATUS_COLOURS["muted"]}'
_OK = f'color:{STATUS_COLOURS["ok"]}'
_ERROR = f'color:{STATUS_COLOURS["error"]}'

# Robot model selector: controls which xacro is passed as the urdf arg to sowbot_sim.launch.py.
# sowbot_01:     TrackedVehicle + TrackController (DART required)
# robo_caatinga: DiffDrive skid-steer (ODE or DART both fine)
# ifarmate:      DiffDrive skid-steer (plugin wiring copied from caatinga)
_ROBOT_MODELS = {
    'sowbot (tracked)': 'sowbot_01.xacro',
    'caatinga (diff drive)': 'robo_caatinga.urdf.xacro',
    'ifarmate (diff drive)': 'ifarmate.urdf.xacro',
}


class GazeboCard(ui.card):
    """Launch, stop and rebuild the Gazebo simulation."""

    def __init__(self, sim_vm: SimViewModel, process_vm: SimProcessViewModel,
                 plant_settings: Callable[[], PlantSettings]) -> None:
        """Build the robot model selector and the launch controls.

        Parameters:
            sim_vm (SimViewModel): Rebuilds the world from the saved map and plant placement.
            process_vm (SimProcessViewModel): Launches, stops and reports on the sim process.
            plant_settings (Callable[[], PlantSettings]): Returns the plant placement to rebuild with.
        """
        super().__init__()
        self._sim_vm = sim_vm
        self._vm = process_vm
        self._plant_settings = plant_settings
        self._xacro = 'sowbot_01.xacro'
        with self:
            ui.label('Gazebo Sim').classes('font-semibold mb-2')
            self._status = ui.label('').classes('text-xs font-mono').style(_MUTED)
            show_status(self._status, process_vm.gazebo_status())
            with ui.row().classes('items-center gap-3 mb-1'):
                ui.html('<span style="font-size:12px;color:#57606a;font-family:monospace">Robot model</span>')
                ui.toggle(
                    list(_ROBOT_MODELS), value='sowbot (tracked)',
                    on_change=self._select_model,
                ).props('dense')
            with ui.row().classes('items-center gap-2 flex-wrap'):
                ui.button('Launch Sim', on_click=lambda: self._launch('normal')).props(
                    'color=positive no-caps').classes('px-4 font-bold')
                ui.button('Rebuild World from Map', on_click=self._rebuild_world).props(
                    'outline no-caps').classes('px-4')
                ui.button('Launch Sim (headless)', on_click=lambda: self._launch('headless')).props(
                    'outline no-caps').classes('px-4')
                ui.button('Launch Sim (browser)', on_click=lambda: self._launch('browser')).props(
                    'outline no-caps').classes('px-4')
                ui.button('Stop Sim', on_click=self._stop).props('outline no-caps').classes('px-4')
                link_button(f'http://{request_host()}:6080/vnc.html', '↗ Gazebo (noVNC)')

    def _select_model(self, event) -> None:
        """Remember the xacro for the selected robot model."""
        self._xacro = _ROBOT_MODELS[event.value]

    def _show(self, text: str, style: str = _MUTED) -> None:
        """Show a status message in the given colour."""
        self._status.set_text(text)
        self._status.style(style)

    async def _launch(self, mode: GazeboMode = 'normal') -> None:
        """Launch the selected simulation and show the result.

        The browser mode starts a virtual display first, so the launch runs off the UI loop.
        """
        show_status(self._status, await ng_run.io_bound(self._vm.launch_gazebo, self._xacro, mode))

    async def _stop(self) -> None:
        """Stop the sim and the display daemons."""
        show_status(self._status, await ng_run.io_bound(self._vm.stop_gazebo))

    async def _rebuild_world(self) -> None:
        """Rebuild maize.world from the saved topology map and the plant placement.

        Plants are studded in the inter-row gaps of the R*_IN/OUT nodes. Gazebo reads the world only
        at launch, so a running sim must be stopped and relaunched to see a rebuild; this refuses
        mid-run rather than silently doing nothing.
        """
        if self._vm.gazebo_running():
            self._show('stop the sim before rebuilding — Gazebo reads the world only at launch', _ERROR)
            return
        try:
            self._show('rebuilding world from map…')
            plant = self._plant_settings()
            weeds = await ng_run.io_bound(
                self._sim_vm.rebuild_world, plant.spacing_m, plant.row_width_m, plant.scale,
                plant.category, plant.model, plant.weed_density)
            if weeds is None:
                self._show('rebuild cancelled', _ERROR)
                return
            self._show(
                f'world rebuilt (spacing={plant.spacing_m:.2f}m, row={plant.row_width_m:.2f}m, '
                f'scale={plant.scale:.2f} on {plant.category}, '
                f'weed density={plant.weed_density}% ({weeds} weeds), '
                f'model={plant.model}) — relaunch to view', _OK)
        except WorldRebuildError as exc:
            self._show(str(exc), _ERROR)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'ERROR: {exc}', _ERROR)
