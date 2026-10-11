from nicegui import ui

from devkit_ui.pages.system.battery_card import BatteryCard
from devkit_ui.pages.system.esp_card import EspCard
from devkit_ui.pages.system.gps_card import GpsCard
from devkit_ui.pages.system.odometry_card import OdometryCard
from devkit_ui.pages.system.safety_card import SafetyCard
from devkit_ui.pages.system.tools_card import ToolsCard
from devkit_ui.view_models.robot_brain_view_model import RobotBrainViewModel
from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel


class SystemPage(ui.column):
    """Robot health and maintenance: odometry, battery, safety, ESP, GPS and dev tools."""

    def __init__(self,
                 telemetry_vm: TelemetryViewModel,
                 robot_brain_vm: RobotBrainViewModel) -> None:
        """Compose the System page from its cards.

        Parameters:
            telemetry_vm (TelemetryViewModel): Source for the odometry, battery, safety and GPS cards.
            robot_brain_vm (RobotBrainViewModel): Receives the ESP commands.
        """
        super().__init__()
        self.classes('w-full gap-3')
        with self:
            with ui.row().classes('items-stretch w-full gap-3'):
                OdometryCard(telemetry_vm).classes('flex-1')
                BatteryCard(telemetry_vm).classes('flex-1')
                SafetyCard(telemetry_vm).classes('flex-1')
            EspCard(robot_brain_vm).classes('w-full')
            GpsCard(telemetry_vm).classes('w-full')
            ToolsCard().classes('w-full')
