from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.domain_services.drive_domain_service import DriveDomainService


class DriveApplicationService:
    """Exposes drive-related user-intent commands.

    Serves as the primary entry point into the drive domain service layer,
    handling lightweight translation or formatting while keeping business logic
    encapsulated within DriveDomainService.
    """

    def __init__(self, drive_service: DriveDomainService) -> None:
        """Store the domain service used to publish drive commands."""
        self._drive = drive_service

    def move_joystick(self, x: float, y: float) -> None:
        """Publish forward speed x (m/s) and negated turn y (rad/s).

        Publisher errors propagate to the caller.
        """
        self._drive.send_speed(x, y)

    def toggle_estop(self, current: bool) -> bool:
        """Publish the request for the inverse of current and return the requested state.

        The driver's acknowledgement, not this return value, confirms the stop.
        """
        return self._drive.toggle_estop(current)

    def register_soft_estop_state_callback(self, callback: Callable[[bool], None]) -> None:
        """Call callback with the soft estop state each time the driver confirms it."""
        self._drive.register_soft_estop_state_callback(callback)
