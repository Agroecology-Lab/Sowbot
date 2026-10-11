from nicegui import ui

from devkit_ui.view_models.robot_brain_view_model import RobotBrainViewModel


class EspCard(ui.card):
    """Command buttons for the robot brain (the ESP32 running Lizard)."""

    def __init__(self, brain: RobotBrainViewModel) -> None:
        """Build the enable, disable, reset, restart and configure buttons.

        Parameters:
            brain (RobotBrainViewModel): Receives the command each button publishes.
        """
        super().__init__()
        with self:
            ui.label('ESP').classes('font-semibold mb-2')
            with ui.row().classes('gap-2 flex-wrap'):
                for label, props, command in (
                    ('Enable', 'color=positive outline no-caps', brain.enable),
                    ('Disable', 'color=negative outline no-caps', brain.disable),
                    ('Reset', 'color=warning outline no-caps', brain.reset),
                    ('Restart', 'color=primary outline no-caps', brain.restart),
                    ('Configure', 'outline no-caps', brain.configure),
                ):
                    ui.button(label, on_click=command).props(props).classes('px-4')
