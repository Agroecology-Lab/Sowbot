from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.application_services.robot_brain_application_service import (
        RobotBrainApplicationService,
    )


class RobotBrainViewModel:
    """UI-facing commands for the robot brain (the ESP32 running Lizard) on the System page."""

    def __init__(self, app_service: RobotBrainApplicationService) -> None:
        """Bind to the application service that publishes the commands."""
        self._app_service = app_service

    def enable(self) -> None:
        """Enable the robot brain."""
        self._app_service.enable()

    def disable(self) -> None:
        """Disable the robot brain."""
        self._app_service.disable()

    def reset(self) -> None:
        """Reset the robot brain."""
        self._app_service.reset()

    def restart(self) -> None:
        """Restart the robot brain."""
        self._app_service.restart()

    def configure(self) -> None:
        """Send the configuration to the robot brain."""
        self._app_service.configure()
