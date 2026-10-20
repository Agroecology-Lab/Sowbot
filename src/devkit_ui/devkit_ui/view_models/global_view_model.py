from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.application_services.drive_application_service import (
        DriveApplicationService,
    )


class GlobalViewModel:
    def __init__(self, drive_app_service: DriveApplicationService) -> None:
        """Bind to the drive service, which holds the soft emergency stop state."""
        self._drive_app_service = drive_app_service

    @property
    def soft_estop_active(self) -> bool:
        """Return the soft estop state the driver last confirmed, False before any confirmation."""
        return self._drive_app_service.soft_estop_active

    def toggle_estop(self) -> None:
        """Request the opposite of the confirmed soft estop state.

        soft_estop_active changes only when the driver confirms the new state, so the button
        never shows STOPPED for a request the driver did not apply. Publish errors propagate.
        """
        self._drive_app_service.toggle_estop(self.soft_estop_active)
