from __future__ import annotations

from typing import TYPE_CHECKING

# Re-exported so pages can catch it without importing the application service layer.
from devkit_ui.application_services.sim_asset_service import SimAssetError

if TYPE_CHECKING:
    from devkit_ui.application_services.sim_asset_service import SimAssetService

__all__ = ['SimAssetError', 'SimViewModel']


class SimViewModel:
    """UI-facing access to the crop models and soil textures the simulated world is built from."""

    def __init__(self, assets: SimAssetService) -> None:
        """Bind to the service that validates and stores the sim assets."""
        self._assets = assets

    def list_crop_models(self) -> list[str]:
        """Return the available crop model names."""
        return self._assets.list_crop_models()

    def has_crop_model(self, name: str) -> bool:
        """Return whether the named crop model exists on disk."""
        return self._assets.has_crop_model(name)

    def validate_mesh(self, data: bytes, filename: str, *, collision: bool = False) -> None:
        """Check an uploaded mesh; raises SimAssetError when it is rejected."""
        self._assets.validate_mesh(data, filename, collision=collision)

    def create_crop_model(self, name: str, visual: bytes | None,
                          collision: bytes | None = None) -> str:
        """Write a new crop model and return its name; raises SimAssetError when rejected."""
        return self._assets.create_crop_model(name, visual, collision)

    def import_soil_zip(self, data: bytes) -> list[str]:
        """Import soil maps from a zip and return their names. Blocking; raises SimAssetError."""
        return self._assets.import_soil_zip(data)

    def classify_map(self, filename: str) -> str:
        """Return the PBR role guessed for a soil texture file."""
        return self._assets.classify_map(filename)
