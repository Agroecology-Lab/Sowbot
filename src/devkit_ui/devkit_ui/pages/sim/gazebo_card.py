# pylint: disable=consider-using-with
import os
import re
import subprocess
from collections.abc import Callable

from nicegui import run as ng_run
from nicegui import ui

from devkit_ui.pages.sim.plant_placement_card import PlantSettings
from devkit_ui.process_utils import (
    alive,
    kill_group,
    link_button,
    request_host,
    restore_label,
    shared,
    spawn_logged,
    start_vnc_stack,
    stop_all,
)
from devkit_ui.view_models.sim_view_model import SimViewModel

_MUTED = 'color:#57606a'
_OK = 'color:#1a7f37'
_ERROR = 'color:#cf222e'

_MAP_FILE = '/workspace/maps/maize_map'

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

    def __init__(self, sim_vm: SimViewModel, plant_settings: Callable[[], PlantSettings]) -> None:
        """Build the robot model selector and the launch controls.

        Parameters:
            sim_vm (SimViewModel): Checks that the selected crop model exists before a rebuild.
            plant_settings (Callable[[], PlantSettings]): Returns the plant placement to rebuild with.
        """
        super().__init__()
        self._sim_vm = sim_vm
        self._plant_settings = plant_settings
        self._xacro = 'sowbot_01.xacro'
        # Handles are shared by every browser session, so a reload cannot orphan a running sim.
        self._gazebo: list = shared('gazebo', lambda: [None])
        self._spawn: list = shared('gazebo_spawn', lambda: [None])
        self._daemons: list = shared('gazebo_daemons', list)  # Xvfb, x11vnc, websockify
        self._env = {
            **os.environ,
            'TMAP2_FILE': _MAP_FILE,
            'GZ_SIM_RESOURCE_PATH': (
                # Forest3D-generated models (model://ground, model://crop/plant) live here; it must
                # come first or gz sim aborts world load with "Unable to find uri".
                '/workspace/models'
                ':/workspace/install/virtual_maize_field/share/virtual_maize_field/models'
                + (':' + os.environ['GZ_SIM_RESOURCE_PATH']
                   if os.environ.get('GZ_SIM_RESOURCE_PATH') else '')
            ),
        }
        with self:
            ui.label('Gazebo Sim').classes('font-semibold mb-2')
            self._status = ui.label('').classes('text-xs font-mono').style(_MUTED)
            restore_label(self._status, self._gazebo[0])
            with ui.row().classes('items-center gap-3 mb-1'):
                ui.html('<span style="font-size:12px;color:#57606a;font-family:monospace">Robot model</span>')
                ui.toggle(
                    list(_ROBOT_MODELS), value='sowbot (tracked)',
                    on_change=self._select_model,
                ).props('dense')
            with ui.row().classes('items-center gap-2 flex-wrap'):
                ui.button('Launch Sim', on_click=self._launch).props(
                    'color=positive no-caps').classes('px-4 font-bold')
                ui.button('Rebuild World from Map', on_click=self._rebuild_world).props(
                    'outline no-caps').classes('px-4')
                ui.button('Launch Sim (headless)', on_click=lambda: self._launch(headless=True)).props(
                    'outline no-caps').classes('px-4')
                ui.button('Launch Sim (browser)', on_click=self._launch_in_browser).props(
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

    def _sim_cmd(self, headless: bool = False) -> list:
        """Build the sowbot_sim launch command for the selected robot model."""
        return [
            'ros2', 'launch', 'devkit_bringup', 'sowbot_sim.launch.py',
            'world:=maize.world',
            f'urdf:={self._xacro}',
            f'headless:={"true" if headless else "false"}',
        ]

    def _launch(self, headless: bool = False) -> None:
        """Launch the selected simulation, optionally headless, and update its status.

        Runs the same command as the CLI: `ros2 launch devkit_bringup sowbot_sim.launch.py
        world:=maize.world urdf:=<selected xacro>`. This replaces the old Launch World / Spawn Robot
        split, which ran sim.launch.py + nav2_only.launch.py instead. That path skipped
        preflight_pkill, fusioncore and kill_bootstrap_tfs entirely (all of which only exist in
        sowbot_sim.launch.py), causing stale wall-time bootstrap TFs to fight the real sim-time TF
        forever and FusionCore to never run at all. Do not reintroduce that split.
        """
        if alive(self._gazebo[0]):
            self._show('already running')
            return
        try:
            self._gazebo[0] = spawn_logged(
                self._sim_cmd(headless), '/tmp/gazebo_sim.log', env=self._env)
            self._show(f'sim launching{" (headless)" if headless else ""} — '
                       f'{self._xacro} — pid {self._gazebo[0].pid}', _OK)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'ERROR: {exc}', _ERROR)

    async def _launch_in_browser(self) -> None:
        """Launch the sim on a virtual display that is viewable through noVNC."""
        if alive(self._gazebo[0]):
            self._show('already running')
            return
        try:
            await ng_run.io_bound(start_vnc_stack, ':99', 5900, 6080, self._daemons)
            # Xvfb has no DRI/GLX driver, so it can't honour whatever hardware-render env manage.py set
            # for the container. Force llvmpipe software GL for this process only (docs/research/Sim.md).
            #
            # Env vars alone are not enough for gz-sim: RViz's Ogre1/GLX renderer honours
            # LIBGL_ALWAYS_SOFTWARE directly, but gz-sim's Ogre2 initialises via EGL_EXT_platform_device,
            # which enumerates /dev/dri and selects a real GPU node. Mesa's software-force guard then
            # refuses to override an explicitly-selected hardware device (the "Not allowed to force
            # software rendering..." warning right before the segfault in gazebo_sim.log). This
            # container's /dev:/dev + privileged mode means a real render node is always visible.
            #
            # Fix: hide /dev/dri from just this subprocess with a private mount namespace, the same
            # trick gz-sim's own CI uses on GPU-less runners.
            env = {
                **self._env,
                'DISPLAY': ':99',
                'LIBGL_ALWAYS_SOFTWARE': '1',
                'GALLIUM_DRIVER': 'llvmpipe',
                'MESA_LOADER_DRIVER_OVERRIDE': 'llvmpipe',
            }
            quoted = ' '.join("'" + a.replace("'", "'\\''") + "'" for a in self._sim_cmd())
            wrapped = [
                'unshare', '--mount', '--propagation', 'private',
                '--', 'bash', '-c',
                f'mount -t tmpfs tmpfs /dev/dri 2>/dev/null; exec {quoted}',
            ]
            # Log to a file so a crashed launch is diagnosable: tail /tmp/gazebo_sim.log.
            self._gazebo[0] = spawn_logged(wrapped, '/tmp/gazebo_sim.log', env=env)
            self._show(f'browser mode — {self._xacro} — pid {self._gazebo[0].pid}', _OK)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'ERROR: {exc}', _ERROR)

    async def _stop(self) -> None:
        """Stop the sim and the display daemons."""
        for handle in (self._gazebo, self._spawn):
            await ng_run.io_bound(kill_group, handle[0])
            handle[0] = None
        await ng_run.io_bound(stop_all, self._daemons)
        self._show('stopped')

    async def _rebuild_world(self) -> None:
        """Rebuild maize.world from the saved topology map and the plant placement.

        Plants are studded in the inter-row gaps of the R*_IN/OUT nodes. Gazebo reads the world only
        at launch, so a running sim must be stopped and relaunched to see a rebuild; this refuses
        mid-run rather than silently doing nothing. It uses the shared worldgen.sh (also called by
        Launch Sim), so Rebuild and Launch Sim agree on the cache key and never regenerate each
        other's world.
        """
        if alive(self._gazebo[0]):
            self._show('stop the sim before rebuilding — Gazebo reads the world only at launch', _ERROR)
            return
        if not os.path.exists(_MAP_FILE):
            self._show(f'no saved map at {_MAP_FILE} — drop/save nodes first', _ERROR)
            return
        try:
            self._show('rebuilding world from map…')
            plant = self._plant_settings()
            if not self._sim_vm.has_crop_model(plant.model):
                self._show(f'model "{plant.model}" not found — upload it first', _ERROR)
                return
            result = await ng_run.io_bound(
                subprocess.run,
                ['bash', '/workspace/worldgen.sh',
                 '--plant-spacing', str(plant.spacing_m),
                 '--row-width', str(plant.row_width_m),
                 '--plant-scale', str(plant.scale),
                 '--scale-category', plant.category,
                 '--crop-model', plant.model,
                 '--weed-density', str(plant.weed_density)],
                capture_output=True, text=True, timeout=120, check=False)
            if result is None:
                self._show('rebuild cancelled', _ERROR)
                return
            if result.returncode != 0:
                error = (result.stderr or result.stdout or 'unknown error').strip()
                self._show(f'rebuild failed: {error[-200:]}', _ERROR)
                return
            match = re.search(r'Weed density:\s*(\d+)', result.stdout or '')
            weeds = match.group(1) if match else '?'
            self._show(
                f'world rebuilt (spacing={plant.spacing_m:.2f}m, row={plant.row_width_m:.2f}m, '
                f'scale={plant.scale:.2f} on {plant.category}, '
                f'weed density={plant.weed_density}% ({weeds} weeds), '
                f'model={plant.model}) — relaunch to view', _OK)
        except subprocess.TimeoutExpired:
            self._show('rebuild timed out', _ERROR)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'ERROR: {exc}', _ERROR)
