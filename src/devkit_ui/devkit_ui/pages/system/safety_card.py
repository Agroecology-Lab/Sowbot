from nicegui import ui

from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel

# (telemetry attribute, label, CSS class suffix while inactive)
_BUMPERS = (
    ('bumper_front_top_active', 'Front top', 'ok'),
    ('bumper_front_bottom_active', 'Front bottom', 'ok'),
    ('bumper_back_active', 'Rear', 'ok'),
)
_ESTOPS = (
    ('estop_front_active', 'Front', 'off'),
    ('estop_back_active', 'Rear', 'off'),
)


class SafetyCard(ui.card):
    """Bumper and hardware e-stop indicators."""

    def __init__(self, telemetry_vm: TelemetryViewModel) -> None:
        """Build the indicator dots and refresh them from the telemetry flags every 0.2 s.

        Parameters:
            telemetry_vm (TelemetryViewModel): Source of the bumper and e-stop flags.
        """
        super().__init__()
        self._telemetry_vm = telemetry_vm
        self._dots: list[tuple[object, str, str]] = []
        with self:
            ui.label('Safety').classes('font-semibold mb-2')
            ui.html('<div class="sec-label">Bumpers</div>')
            self._add_indicators(_BUMPERS)
            ui.html('<div class="sec-label mt-3">E-stops</div>')
            self._add_indicators(_ESTOPS)
            ui.timer(0.2, self.refresh)

    def _add_indicators(self, indicators: tuple[tuple[str, str, str], ...]) -> None:
        """Add one dot and label per indicator."""
        for attr, label, idle in indicators:
            with ui.row().classes('items-center gap-0'):
                dot = ui.html('<span class="dot-off"></span>')
                ui.label(label).classes('text-sm')
            self._dots.append((dot, attr, idle))

    def refresh(self) -> None:
        """Show each active flag as a warning dot and each inactive one in its idle colour."""
        for dot, attr, idle in self._dots:
            state = 'warn' if getattr(self._telemetry_vm, attr) else idle
            dot.set_content(f'<span class="dot-{state}"></span>')
