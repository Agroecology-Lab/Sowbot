"""Rebuild the Gazebo world from the saved topology map.

Runs the shared /workspace/worldgen.sh, the same script Launch Sim uses, so Rebuild and Launch
Sim agree on the cache key and never regenerate each other's world. No ROS or NiceGUI imports.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from devkit_ui.constants import SIM_MAP_FILE

if TYPE_CHECKING:
    from devkit_ui.application_services.sim_asset_service import SimAssetService

WORLDGEN_SCRIPT = '/workspace/worldgen.sh'
WORLDGEN_TIMEOUT_SEC = 120


class WorldRebuildError(Exception):
    """The world could not be rebuilt; the message is suitable for showing to the user."""


class SimWorldService:
    """Validate the inputs and run worldgen.sh for a world rebuild."""

    def __init__(self, assets: SimAssetService, map_file: str = SIM_MAP_FILE,
                 script: str = WORLDGEN_SCRIPT, run: Callable = subprocess.run) -> None:
        """Bind to the asset service and the worldgen process boundary.

        Parameters:
            assets (SimAssetService): Checks that the selected crop model exists.
            map_file (str): The saved topology map the world is built from.
            script (str): The worldgen script to run.
            run (Callable): subprocess.run, replaceable in tests.
        """
        self._assets = assets
        self._map_file = map_file
        self._script = script
        self._run = run

    def rebuild(self, spacing_m: float, row_width_m: float, scale: float,
                category: str, model: str, weed_density: int) -> str:
        """Rebuild maize.world and return the weed count worldgen reported ('?' if none).

        Blocking: call off the UI loop. Gazebo reads the world only at launch, so the caller
        must make sure the sim is stopped first.

        Parameters:
            spacing_m: Distance between plants along a row, in meters.
            row_width_m: Fallback crop row width, in meters.
            scale: Uniform model scale factor; 1.0 keeps the original size.
            category: Category to scale: all, crop, weed, or irrigation.
            model: Crop model folder name.
            weed_density: Percentage of the generator's maximum weeds per crop; 0 disables weeds.

        The script may reuse a cached world, in which case no weed count is reported.
        Numeric values and category are forwarded without validation here.

        Raises:
            WorldRebuildError: No saved map, unknown crop model, worldgen failed or timed out.
            OSError: The worldgen process could not be started.
        """
        if not os.path.exists(self._map_file):
            raise WorldRebuildError(f'no saved map at {self._map_file} — drop/save nodes first')
        if not self._assets.has_crop_model(model):
            raise WorldRebuildError(f'model "{model}" not found — upload it first')
        try:
            result = self._run(
                ['bash', self._script,
                 '--plant-spacing', str(spacing_m),
                 '--row-width', str(row_width_m),
                 '--plant-scale', str(scale),
                 '--scale-category', category,
                 '--crop-model', model,
                 '--weed-density', str(weed_density)],
                capture_output=True, text=True, timeout=WORLDGEN_TIMEOUT_SEC, check=False)
        except subprocess.TimeoutExpired as exc:
            raise WorldRebuildError('rebuild timed out') from exc
        if result.returncode != 0:
            error = (result.stderr or result.stdout or 'unknown error').strip()
            raise WorldRebuildError(f'rebuild failed: {error[-200:]}')
        match = re.search(r'Weed density:\s*(\d+)', result.stdout or '')
        return match.group(1) if match else '?'
