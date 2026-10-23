from nicegui import ui

from devkit_ui.pages.obstacle_cards import attach_mission_sidebar_controls
from devkit_ui.pages.plan.repair_dialog import ask_repair_base
from devkit_ui.plan_types import MIN_CORNERS
from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel
from devkit_ui.view_models.plan_view_model import (
    STATUS_ERROR,
    STATUS_OK,
    PlanPageState,
    PlanViewModel,
)

DEFAULT_RECON_PATH = '/workspace/maps/recon_logs/recon.csv'
MUTED = 'color:#8c959f'
STATUS_COLOURS = {STATUS_OK: 'color:#1a7f37', STATUS_ERROR: 'color:#cf222e'}
INFO_COLOUR = 'color:#57606a'


class PlanControlsCard(ui.card):
    """Planner settings, obstacle draw controls and the Plan, Save, Import, Clear and Repair actions."""

    def __init__(self, state: PlanPageState, plan_vm: PlanViewModel,
                 obstacle_vm: ObstacleViewModel, mission_map) -> None:
        """Build the controls and keep their labels and buttons in step with the page state.

        Parameters:
            state (PlanPageState): The boundary, draft and status this page shows and changes.
            plan_vm (PlanViewModel): Plans, imports, saves and repairs rows.
            obstacle_vm (ObstacleViewModel): Supplies the obstacle rings to plan around.
            mission_map: The boundary map; the obstacle controls draw on it and a click on it
                adds a boundary corner or an obstacle point, depending on the draw mode.
        """
        super().__init__()
        self.style('width:220px;flex-shrink:0;padding:14px')
        self._state = state
        self._plan_vm = plan_vm
        self._obstacle_vm = obstacle_vm
        self._map = mission_map
        self._save_status_seen = ''

        with self:
            self._build_planner_settings()
            # Map clicks are wired by attach_mission_sidebar_controls: it calls state.add_corner
            # in boundary mode and the obstacle view model otherwise. Do NOT add a second
            # 'map-click' handler to the map; it would fire alongside and double-draw.
            self.draw_handle = attach_mission_sidebar_controls(
                obstacle_vm, mission_map, state.add_corner)
            self._obstacle_pad = self.draw_handle.obstacle_pad
            self._build_actions()

        state.subscribe(self.refresh)
        self.refresh()
        ui.timer(0.4, self._refresh_save_status)

    def _build_planner_settings(self) -> None:
        """Add the tool width, row angle, contour, headland, snake and row naming inputs."""
        ui.html('<div class="sec-label">Tool width</div>')
        self._width = ui.number(value=1.2, min=0.1, max=10.0, step=0.1, precision=2,
                                suffix='m').classes('w-full')

        ui.html('<div class="sec-label mt-3">Row angle</div>')
        self._angle = ui.slider(min=0, max=179, step=1, value=0).classes('w-full')
        angle_lbl = ui.label('0°').classes('text-xs font-mono').style(INFO_COLOUR)
        self._angle.on('update:model-value', lambda e: angle_lbl.set_text(f'{int(e.args)}°'))

        # CONTOUR: terrain-following rows instead of one fixed angle, offset from a reference
        # elevation isoline (dem.select_reference_contour_latlon()) rather than F2C's
        # SG_BruteForce. See f2c_planner.run_contour_f2c()'s module comment for why this
        # isn't a config flag on F2C.
        ui.html('<div class="sec-label mt-3">Contour rows</div>')
        self._contour = ui.checkbox('follow terrain (uses recon elevation log)', value=False)
        self._recon_path = ui.input(
            value=DEFAULT_RECON_PATH, placeholder=DEFAULT_RECON_PATH).classes('w-full mt-1')
        dem_res_lbl = ui.html('<div class="sec-label mt-1">DEM grid resolution</div>')
        self._dem_res = ui.number(value=1.0, min=0.2, max=5.0, step=0.1, precision=2,
                                  suffix='m').classes('w-full')
        contour_note = ui.label(
            'Row angle is ignored in contour mode — row direction '
            'follows the reference elevation line instead.'
        ).classes('text-xs').style(MUTED)
        self._contour_only = (self._recon_path, dem_res_lbl, self._dem_res, contour_note)
        self._on_contour_toggle(False)
        self._contour.on_value_change(lambda e: self._on_contour_toggle(bool(e.value)))

        # HEADLAND: shrink the cover area by this much on all sides so swaths don't start or
        # end at the field boundary. 0 = off.
        ui.html('<div class="sec-label mt-3">Headland width</div>')
        self._headland = ui.number(value=0.0, min=0.0, max=5.0, step=0.1, precision=2,
                                   suffix='m').classes('w-full')
        ui.label('0 = no inset; ≈ tool width for one-row headland').classes(
            'text-xs').style(MUTED)

        # SNAKE: reverse every other swath so the end of N is near the start of N+1.
        ui.html('<div class="sec-label mt-3">Snake order</div>')
        self._snake = ui.checkbox('reverse every other row', value=True)

        ui.html('<div class="sec-label mt-3">First row ID</div>')
        self._row_id_start = ui.number(value=1, min=1, step=1, precision=0).classes('w-full')

        ui.html('<div class="sec-label mt-3">Row name prefix</div>')
        self._prefix = ui.input(value='F2C', placeholder='F2C').classes('w-full')
        ui.label('→ {prefix}_R{n}_IN / _OUT').classes('text-xs font-mono').style(MUTED)

    def _build_actions(self) -> None:
        """Add the corner count, the action buttons and the two status lines."""
        ui.separator().classes('my-3')
        self._corners_lbl = ui.label('').classes('text-xs font-mono').style(INFO_COLOUR)

        self._plan_btn = ui.button('Plan Rows', on_click=self._plan).props(
            'color=positive no-caps').classes('w-full mt-2')
        self._save_btn = ui.button('Save as Topo Rows', on_click=self._save).props(
            'color=primary no-caps').classes('w-full mt-1')
        ui.upload(label='Import web plan (.geojson)', auto_upload=True,
                  on_upload=self._import).props('accept=.geojson,.json').classes('w-full mt-1')
        self._overwrite = ui.checkbox(
            'Overwrite existing rows with same prefix', value=False).classes('text-xs mt-1')
        ui.button('Clear', on_click=self._clear).props('outline no-caps').classes('w-full mt-1')
        ui.button('Repair Connectivity', on_click=self._repair).props(
            'outline no-caps').classes('w-full mt-1').tooltip(
                'Wire navigate_to_pose edges between consecutive row '
                'IN/OUT pairs. Splices into current node if localised.')

        self._status_lbl = ui.label('').classes('text-xs font-mono mt-2').style(
            f'{INFO_COLOUR};word-break:break-word')
        self._save_lbl = ui.label('').classes('text-xs font-mono mt-1').style(
            f'{INFO_COLOUR};word-break:break-word')

    def _on_contour_toggle(self, on: bool) -> None:
        """Show the contour-only inputs and disable the row angle when contour mode is on."""
        self._angle.set_enabled(not on)
        for element in self._contour_only:
            element.set_visibility(on)

    def refresh(self) -> None:
        """Show the page state: corner count, status line and which buttons are enabled."""
        count = len(self._state.corners)
        self._corners_lbl.set_text(
            f'{count} corner{"s" if count != 1 else ""}'
            + (' ✓' if count >= MIN_CORNERS else f' — need {MIN_CORNERS}+'))
        self._status_lbl.set_text(self._state.status)
        self._status_lbl.style(STATUS_COLOURS.get(self._state.status_kind, INFO_COLOUR))
        self._plan_btn.set_enabled(self._state.can_plan)
        self._save_btn.set_enabled(self._state.can_save)

    def _refresh_save_status(self) -> None:
        """Show this page's save or repair status when it changes."""
        current = self._state.save_status
        if current == self._save_status_seen:
            return
        self._save_status_seen = current
        self._save_lbl.set_text(current)
        self._save_lbl.style(
            STATUS_COLOURS[STATUS_ERROR] if current.startswith('ERROR') else
            STATUS_COLOURS[STATUS_OK] if current else INFO_COLOUR)

    def _form(self) -> dict:
        """Return the raw planner settings as PlanRequest.from_form takes them."""
        return {
            'tool_width': self._width.value, 'angle_deg': self._angle.value,
            'headland_m': self._headland.value, 'snake': self._snake.value,
            'obstacle_pad_m': self._obstacle_pad.value, 'contour': self._contour.value,
            'recon_path': self._recon_path.value, 'dem_res': self._dem_res.value,
        }

    async def _plan(self) -> None:
        """Plan rows for the drawn boundary without blocking; the state discards stale results."""
        run = self._state.begin_plan(self._obstacle_vm.rings_ll(), **self._form())
        if run is None:
            return
        view = await self._plan_vm.plan(run.request)
        self._state.finish_plan(run, view)

    async def _import(self, e) -> None:
        """Read an uploaded web-planner plan and take it as the draft."""
        data = (await e.file.read()) if hasattr(e, 'file') else e.content.read()
        self._state.apply_import(self._plan_vm.import_plan(data.decode('utf-8', errors='replace')))

    def _save(self) -> None:
        """Save the draft using the current prefix, first row ID and overwrite option."""
        self._plan_vm.save_rows(
            self._state.draft,
            self._prefix.value or 'F2C',
            int(self._row_id_start.value or 1),
            overwrite=self._overwrite.value,
            on_status=self._state.set_save_status,
        )

    def _clear(self) -> None:
        """Drop the boundary, the draft and any obstacle polygon being drawn."""
        self._state.clear()
        # OBSTACLE: also tear down any in-progress obstacle polygon.
        self.draw_handle.clear_in_progress()
        self._map.set_center(self._map.center)

    async def _repair(self) -> None:
        """Ask for confirmation, then repair missing row edges in the loaded map.

        The optional base node is validated and named in the status; repair does not add
        splice edges to it. Return without opening a dialog if no map is loaded.
        """
        offer = self._plan_vm.repair_offer()
        if offer is None:
            return
        base = await ask_repair_base(offer)
        if base is None:
            return
        self._plan_vm.repair(base or None, self._state.set_save_status)
