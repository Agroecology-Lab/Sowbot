from collections.abc import Callable

from nicegui import ui

from devkit_ui.actions import ACTIONS
from devkit_ui.view_models.mission_view_model import MissionQueue
from devkit_ui.view_models.plan_view_model import PlanViewModel

MUTED = 'color:#8c959f'
GREY = 'color:#57606a'
RED = 'color:#cf222e'
PANEL = 'background:#f6f8fa;padding:10px'
_UNSET = object()


class MissionQueueCard(ui.card):
    """Pick an implement, queue rows from the map, and run or cancel the queued mission."""

    def __init__(self, queue: MissionQueue, plan_vm: PlanViewModel,
                 on_run: Callable[[list, object], object], on_cancel: Callable[[], object],
                 on_delete_row: Callable[[int], object]) -> None:
        """Build the action picker, the available rows, the queue and the Run and Cancel buttons.

        Parameters:
            queue (MissionQueue): This session's steps. The queue list redraws when it changes.
            plan_vm (PlanViewModel): Says which rows the loaded map has.
            on_run (Callable): Called with the queue's steps and the status label when Run
                Mission is pressed; it reports progress and errors in that label.
            on_cancel (Callable): Called when Cancel is pressed.
            on_delete_row (Callable): Called with a row id when its delete button is pressed;
                it may be a coroutine function that asks for confirmation.
        """
        super().__init__()
        self.classes('w-full')
        self._queue = queue
        self._plan_vm = plan_vm
        self._on_delete_row = on_delete_row
        self._param_inputs: dict = {}
        self._seen = _UNSET
        self._built = _UNSET

        with self:
            with ui.row().classes('items-baseline gap-2 mb-3'):
                ui.label('Mission Queue').classes('font-semibold')
                ui.label('select rows · pick action · run').classes('text-xs').style(MUTED)

            with ui.row().classes('items-center gap-3 w-full mb-2 flex-wrap'):
                ui.html('<div class="sec-label" style="white-space:nowrap">Implement</div>')
                self._action = ui.select(
                    options={k: f'{a.icon} {a.label}' for k, a in ACTIONS.items()},
                    value='drive',
                ).classes('flex-1').props('dense outlined')

            # The parameter inputs are rebuilt whenever the action changes.
            self._param_row = ui.row().classes('items-end gap-3 w-full flex-wrap mb-2')
            self._action.on_value_change(lambda _: self._rebuild_params())
            self._rebuild_params()

            with ui.row().classes('w-full gap-4 items-start'):
                with ui.card().classes('flex-1').style(PANEL):
                    ui.html('<div class="sec-label mb-2">Available rows</div>')
                    self._available_col = ui.column().style('gap:2px;width:100%')
                with ui.card().classes('flex-1').style(PANEL):
                    ui.html('<div class="sec-label mb-2">Today\'s queue</div>')
                    self._queue_col = ui.column().style('gap:2px;width:100%')
                    self._queue_lbl = ui.label('Empty — add rows from the left').classes(
                        'text-xs').style(MUTED)

            self._status_lbl = ui.label('').classes('text-xs font-mono mt-3').style(GREY)
            with ui.row().classes('gap-2 mt-3'):
                self.run_btn = ui.button(
                    'Run Mission', on_click=lambda: on_run(queue.items, self._status_lbl),
                ).props('color=positive no-caps')
                ui.button('Cancel', on_click=on_cancel).props('color=negative no-caps flat')

        queue.subscribe(self._render_queue)
        self._render_queue()
        self._refresh_available()
        ui.timer(1.0, self._refresh_available)

    def set_run_enabled(self, enabled: bool) -> None:
        """Enable or disable Run Mission, which is off while a mission is running."""
        self.run_btn.set_enabled(enabled)

    def _rebuild_params(self) -> None:
        """Show an input for each parameter of the selected action."""
        self._param_row.clear()
        self._param_inputs.clear()
        action = ACTIONS.get(self._action.value)
        if action is None or not action.param_schema:
            return
        with self._param_row:
            for param in action.param_schema:
                self._param_inputs[param.key] = ui.number(
                    label=f'{param.label} ({param.unit})', value=param.default,
                    min=param.min, max=param.max, step=param.step, precision=param.precision,
                ).classes('w-32').props('dense outlined')

    def _add_row(self, row_id: int) -> None:
        """Queue a row with the selected action and the parameter values entered."""
        key = self._action.value or 'drive'
        action = ACTIONS.get(key)
        params = {p.key: float(self._param_inputs[p.key].value)
                  for p in (action.param_schema if action else ())
                  if p.key in self._param_inputs}
        self._queue.add(row_id, key, params)

    def _render_queue(self) -> None:
        """Redraw the queued steps with their move and remove buttons."""
        items = self._queue.items
        self._queue_col.clear()
        self._queue_lbl.set_visibility(not items)
        with self._queue_col:
            for index, (row_id, key, params) in enumerate(items):
                action = ACTIONS.get(key)
                param_text = ' · '.join(
                    f'{p.label}: {params.get(p.key, p.default):.{p.precision}f}{p.unit}'
                    for p in (action.param_schema if action else ()))
                with ui.row().classes('items-center gap-1 w-full'):
                    with ui.column().classes('flex-1 gap-0'):
                        ui.label(
                            f'Row {row_id}  {action.icon if action else "?"} '
                            f'{action.label if action else key}'
                        ).classes('text-sm font-mono')
                        if param_text:
                            ui.label(param_text).classes('text-xs font-mono').style(MUTED)
                    ui.button('↑', on_click=lambda _, i=index: self._queue.move(i, -1)).props(
                        'flat dense').classes('text-xs').style(GREY).set_enabled(index > 0)
                    ui.button('↓', on_click=lambda _, i=index: self._queue.move(i, 1)).props(
                        'flat dense').classes('text-xs').style(GREY).set_enabled(
                            index < len(items) - 1)
                    ui.button('✕', on_click=lambda _, i=index: self._queue.remove(i)).props(
                        'flat dense').classes('text-xs').style(RED)

    def _refresh_available(self) -> None:
        """Redraw the available rows once the loaded map has stopped changing.

        The map is compared on each tick. A new map is drawn on the next tick that still sees
        it, so a map that is being rewritten is not drawn half-built.
        """
        snapshot = self._plan_vm.topo_snapshot()
        settled = snapshot == self._seen
        self._seen = snapshot
        if not settled and self._built is not _UNSET:
            return
        if snapshot == self._built:
            return
        self._built = snapshot
        rows = self._plan_vm.available_rows()
        self._available_col.clear()
        with self._available_col:
            if not rows:
                ui.label('No rows in map yet').classes('text-xs').style(MUTED)
                return
            for row_id in sorted(rows):
                with ui.row().classes('items-center gap-2 w-full'):
                    ui.label(f'Row {row_id}').classes('text-sm font-mono flex-1')
                    ui.label(rows[row_id]).classes('text-xs font-mono').style(MUTED)
                    ui.button('Add →', on_click=lambda _, r=row_id: self._add_row(r)).props(
                        'color=primary outline no-caps dense')
                    ui.button('✕', on_click=lambda _, r=row_id: self._on_delete_row(r)).props(
                        'flat dense').classes('text-xs').style(RED)
