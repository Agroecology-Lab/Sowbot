from __future__ import annotations

from typing import TYPE_CHECKING

# Re-exported so pages can catch these without importing the application service layer.
from devkit_ui.application_services.sim_asset_service import SimAssetError
from devkit_ui.application_services.sim_world_service import WorldRebuildError

if TYPE_CHECKING:
    from devkit_ui.application_services.sim_asset_service import SimAssetService
    from devkit_ui.application_services.sim_world_service import SimWorldService

__all__ = ['SimAssetError', 'SimViewModel', 'WorldRebuildError']


class SimViewModel:
    """UI-facing access to the crop models and soil textures the simulated world is built from."""

    def __init__(self, assets: SimAssetService, world: SimWorldService) -> None:
        """Bind to the services that store the sim assets and rebuild the world from them."""
        self._assets = assets
        self._world = world

    def list_crop_models(self) -> list[str]:
        """Return sorted folders containing model.sdf, or ['plant'] when none qualify."""
        return self._assets.list_crop_models()

    def has_crop_model(self, name: str) -> bool:
        """Return whether the named crop model exists on disk."""
        return self._assets.has_crop_model(name)

    def validate_mesh(self, data: bytes, filename: str, *, collision: bool = False) -> None:
        """Check a mesh's extension, initial bytes, and 50 MiB size limit.

        Raise SimAssetError on rejection; this does not fully parse glTF data.
        collision only changes the oversized-file error message.
        """
        self._assets.validate_mesh(data, filename, collision=collision)

    def create_crop_model(self, name: str, visual: bytes | None,
                          collision: bytes | None = None) -> str:
        """Write a new crop model and return its stripped name.

        Use visual for collision geometry when collision is None; validate meshes separately.
        Raise SimAssetError for an invalid or existing name or a missing visual mesh.
        Write errors propagate as OSError after attempted cleanup of the partial model.
        """
        return self._assets.create_crop_model(name, visual, collision)

    def import_soil_zip(self, data: bytes) -> list[str]:
        """Replace the imported soil textures with ZIP image maps and return their base names.

        Flatten folders and keep only .jpg, .jpeg, and .png files.
        Raise SimAssetError for an invalid ZIP or no matching images, preserving old textures.
        Extraction and filesystem errors propagate and may leave a partial replacement.
        Blocking: call off the UI loop.
        """
        return self._assets.import_soil_zip(data)

    def classify_map(self, filename: str) -> str:
        """Return the PBR role guessed for a soil texture file."""
        return self._assets.classify_map(filename)

    def rebuild_world(self, spacing_m: float, row_width_m: float, scale: float,
                      category: str, model: str, weed_density: int) -> str:
        """Rebuild the world from the saved map and return the weed count worldgen reported.

        Return '?' if no count is reported, including when the cached world is reused.
        See SimWorldService.rebuild for placement units and parameter meanings.
        Blocking: call off the UI loop with the simulation stopped.
        Raise WorldRebuildError for missing inputs, a failed script, or a timeout;
        process-start errors propagate as OSError.
        """
        return self._world.rebuild(spacing_m, row_width_m, scale, category, model, weed_density)
