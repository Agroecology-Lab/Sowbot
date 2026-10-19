"""Disk access for the files the Gazebo world is built from (crop models, soil textures).

SimAssetService decides what may be written; this store only reads and writes the folders.
No ROS or NiceGUI imports.
"""
from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

CROP_MODELS_DIR = Path('/workspace/models/crop')
SOIL_TEXTURES_DIR = Path('/workspace/uploads/soil_custom/textures')


class SimAssetStore:
    """Reads and writes the crop model folders and the imported soil texture folder."""

    def __init__(self, crop_models_dir: Path = CROP_MODELS_DIR,
                 soil_textures_dir: Path = SOIL_TEXTURES_DIR) -> None:
        """Keep the directories crop models and soil textures are stored in."""
        self._crop_dir = crop_models_dir
        self._soil_dir = soil_textures_dir

    def crop_model_names(self) -> list[str]:
        """Return the sorted crop model folders that have a model.sdf; empty if none."""
        if not self._crop_dir.exists():
            return []
        return [d.name for d in sorted(self._crop_dir.iterdir())
                if d.is_dir() and (d / 'model.sdf').exists()]

    def has_crop_model(self, name: str) -> bool:
        """Return whether name is a crop model folder containing a model.sdf."""
        model_dir = self._crop_dir / name
        return model_dir.is_dir() and (model_dir / 'model.sdf').is_file()

    def name_taken(self, name: str) -> bool:
        """Return whether a file or folder called name is already in the crop directory."""
        return (self._crop_dir / name).exists()

    def write_crop_model(self, name: str, meshes: Mapping[str, bytes],
                         config: str, sdf: str) -> None:
        """Write a crop model folder: meshes under mesh/, then model.config and model.sdf.

        A model that fails part way through is removed, so the name can be used again.
        OSError propagates from writing.
        """
        model_dir = self._crop_dir / name
        try:
            mesh_dir = model_dir / 'mesh'
            mesh_dir.mkdir(parents=True)
            for filename, data in meshes.items():
                (mesh_dir / filename).write_bytes(data)
            (model_dir / 'model.config').write_text(config, encoding='utf-8')
            (model_dir / 'model.sdf').write_text(sdf, encoding='utf-8')
        except BaseException:
            shutil.rmtree(model_dir, ignore_errors=True)
            raise

    def clear_soil_textures(self) -> None:
        """Remove the imported soil textures and recreate the empty texture folder."""
        if self._soil_dir.exists():
            shutil.rmtree(self._soil_dir)
        self._soil_dir.mkdir(parents=True, exist_ok=True)

    def open_soil_texture(self, name: str) -> BinaryIO:
        """Open a file called name in the texture folder for writing; the caller closes it."""
        return open(self._soil_dir / name, 'wb')  # pylint: disable=consider-using-with
