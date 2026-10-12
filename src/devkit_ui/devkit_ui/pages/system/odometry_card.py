from nicegui import ui

from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel


class OdometryCard(ui.card):
    """Measured linear and angular velocity, as derived from odometry."""

    def __init__(self, telemetry_vm: TelemetryViewModel) -> None:
        """Bind two read-only sliders to the measured velocities.

        The values only move while something calls TelemetryViewModel.refresh(); the Run
        page's update timer does that.

        Parameters:
            telemetry_vm (TelemetryViewModel): Source of the measured velocities.
        """
        super().__init__()
        with self:
            ui.label('Odometry').classes('font-semibold mb-2')
            ui.html('<div class="sec-label">Linear velocity</div>')
            ui.slider(min=-1, max=1, step=0.05, value=0).props(
                'readonly selection-color=transparent color=green').bind_value_from(
                    telemetry_vm, 'linear_velocity')
            ui.html('<div class="sec-label mt-2">Angular velocity</div>')
            ui.slider(min=-1, max=1, step=0.05, value=0).props(
                'readonly selection-color=transparent color=green').bind_value_from(
                    telemetry_vm, 'angular_velocity')
