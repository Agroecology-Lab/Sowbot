from collections.abc import Callable

from nicegui import ui

from devkit_ui.actions import ACTIONS
from devkit_ui.view_models.mission_view_model import MissionQueue, MissionViewModel

MUTED = 'color:#8c959f'
GREEN, RED, AMBER, GREY = 'color:#1a7f37', 'color:#cf222e', 'color:#9a6700', 'color:#57606a'


class MissionStoreCard(ui.column):
    """Saved missions with their repeat schedules, and a form to save the queue as a new one."""

    def __init__(self, mission_vm: MissionViewModel, queue: MissionQueue,
                 set_run_enabled: Callable[[bool], object]) -> None:
        """Build the saved-missions list and the save-queue form.

        Parameters:
            mission_vm (MissionViewModel): The saved missions, their status and the actions on them.
            queue (MissionQueue): The steps the Save button stores as a mission.
            set_run_enabled (Callable): Called as the list refreshes, with False while a mission
                is running, so the Run Mission button cannot start a second one.
        """
        super().__init__()
        self.classes('w-full')
        self._vm = mission_vm
        self._queue = queue
        self._set_run_enabled = set_run_enabled
        self._version = -1

        with self:
            with ui.row().classes('items-baseline gap-2 mb-2'):
                ui.label('Mission Store').classes('font-semibold')
                ui.label('saved missions with repeat schedules').classes('text-xs').style(MUTED)

            with ui.card().classes('w-full').style('background:#f6f8fa;padding:10px'):
                self._missions_col = ui.column().style('gap:2px;width:100%')
                self._empty_lbl = ui.label('No saved missions').classes('text-xs').style(MUTED)

            # Save the current queue as a named, optionally recurring, mission.
            with ui.row().classes('items-center gap-2 mt-2 flex-wrap'):
                self._name = ui.input(
                    placeholder='Mission name', label='Save queue as…',
                ).classes('flex-1').props('dense outlined')
                self._repeat = ui.number(
                    label='Repeat (h)', value=None, min=1, step=1, precision=0,
                ).classes('w-24').props('dense outlined clearable').tooltip(
                    'Leave blank for one-shot')
                ui.button('Save', on_click=self._save).props('color=primary no-caps dense')

            self._status_lbl = ui.label('').classes('text-xs font-mono mt-1').style(GREY)

        self._refresh()
        ui.timer(0.5, self._refresh)

    def _save(self) -> None:
        """Save the queue using the entered name and optional repeat interval in hours."""
        repeat = int(self._repeat.value) if self._repeat.value else None
        self._vm.save_queue(self._queue.items, self._name.value or '', repeat)

    def _refresh(self) -> None:
        """Show status and Run button state; rebuild the saved list if the store changed."""
        status = self._vm.status
        self._status_lbl.set_text(status)
        self._status_lbl.style(RED if status.startswith('ERROR') else GREEN if status else GREY)
        self._set_run_enabled(not self._vm.running)

        version = self._vm.version
        if version == self._version:
            return
        self._version = version

        missions = self._vm.missions
        self._missions_col.clear()
        self._empty_lbl.set_visibility(not missions)
        with self._missions_col:
            for mission in missions:
                self._add_mission(mission)

    def _add_mission(self, mission: dict) -> None:
        """Add one saved mission's row: action, name, size, next run, last result and controls."""
        mission_id = mission.get('id', '?')
        action = ACTIONS.get(mission.get('action', '—'))
        due = self._vm.next_due_in_hours(mission_id)
        if due is None:
            due_text, due_style = 'done', MUTED
        elif due == 0.0:
            due_text, due_style = 'due now', GREEN
        else:
            due_text, due_style = f'in {due:.1f}h', AMBER
        last_ok = mission.get('last_run_success')

        with ui.row().classes('items-center gap-2 w-full'):
            ui.label(f'{action.icon if action else "?"} {mission.get("name", mission_id)}').classes(
                'text-sm font-mono').style('min-width:100px')
            ui.label(f'{len(mission.get("rows", []))} rows').classes(
                'text-xs font-mono flex-1').style(MUTED)
            ui.label(due_text).classes('text-xs font-mono').style(due_style)
            ui.label('✓' if last_ok is True else '✗' if last_ok is False else '—').classes(
                'text-xs font-mono').style(GREEN if last_ok is True else
                                           RED if last_ok is False else MUTED)
            active = ui.checkbox('', value=mission.get('active', False)).props('dense')
            active.tooltip('Active — included in today_queue()')
            active.on_value_change(lambda e, m=mission_id: self._vm.set_active(m, e.value))
            ui.button('✕', on_click=lambda _, m=mission_id: self._vm.delete(m)).props(
                'flat dense').classes('text-xs').style(RED)
