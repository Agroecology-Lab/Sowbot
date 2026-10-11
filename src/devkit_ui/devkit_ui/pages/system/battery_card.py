from nicegui import ui

from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel


class BatteryCard(ui.card):
    """Battery level and voltage."""

    def __init__(self, telemetry_vm: TelemetryViewModel) -> None:
        """Bind the battery text to the telemetry view model.

        Parameters:
            telemetry_vm (TelemetryViewModel): Source of the formatted battery text.
        """
        super().__init__()
        with self:
            ui.label('Battery').classes('font-semibold mb-2')
            ui.label().classes('text-sm').bind_text_from(telemetry_vm, 'battery_text')
