from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.application_services.drive_application_service import (
        DriveApplicationService,
    )


class GlobalViewModel:
    def __init__(self, drive_app_service: DriveApplicationService) -> None:
        """Initialize with the soft emergency stop inactive until the driver confirms a state."""
        self.soft_estop_active: bool = False
        self._drive_app_service = drive_app_service
        drive_app_service.register_soft_estop_state_callback(self._on_soft_estop_state)

    def toggle_estop(self) -> None:
        """Request the opposite of the confirmed soft estop state.

        soft_estop_active changes only when the driver confirms the new state, so the button
        never shows STOPPED for a request the driver did not apply. Publish errors propagate.
        """
        self._drive_app_service.toggle_estop(self.soft_estop_active)

    def _on_soft_estop_state(self, active: bool) -> None:
        """Store the soft estop state the driver confirmed."""
        self.soft_estop_active = active
