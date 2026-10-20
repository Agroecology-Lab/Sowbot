"""Create and import the files the Gazebo world is built from (crop models, soil textures).

This is the file-only half of the Sim page's controls: it validates
uploads and hands them to a SimAssetStore to write. Launching and stopping processes is separate.
No ROS or NiceGUI imports.
"""
from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devkit_ui.sim_asset_store import SimAssetStore

MAX_MESH_BYTES = 50 * 1024 * 1024
# Forest3D skips .exr, so only image types it can load are harvested from soil zips.
SOIL_MAP_SUFFIXES = ('.jpg', '.jpeg', '.png')

_MODEL_NAME_RE = re.compile(r'[a-zA-Z0-9_]+')

_MODEL_CONFIG = '''<?xml version="1.0"?>
<model>
    <name>{name}</name>
    <version>1.0</version>
    <sdf version="1.8">model.sdf</sdf>
    <author>
        <name>User Upload</name>
    </author>
    <description>{name} plant model</description>
</model>
'''

_MODEL_SDF = '''<?xml version="1.0" ?>
<sdf version="1.8">
    <model name="{name}">
        <static>true</static>
        <link name="link">
            <collision name="collision">
                <geometry>
                    <mesh><uri>mesh/{collision}</uri><scale>1 1 1</scale></mesh>
                </geometry>
            </collision>
            <visual name="visual">
                <geometry>
                    <mesh><uri>mesh/{visual}</uri><scale>1 1 1</scale></mesh>
                </geometry>
            </visual>
        </link>
    </model>
</sdf>
'''


class SimAssetError(Exception):
    """An upload or request was rejected; str(error) is the message to show."""


class SimAssetService:
    """Validates and writes crop models and soil textures for the simulated world."""

    def __init__(self, store: SimAssetStore) -> None:
        """Keep the store crop models and soil textures are read from and written to."""
        self._store = store

    def list_crop_models(self) -> list[str]:
        """Return the crop model folders that have a model.sdf, or ['plant'] if none."""
        return self._store.crop_model_names() or ['plant']

    def has_crop_model(self, name: str) -> bool:
        """Return whether name is a crop model folder containing a model.sdf."""
        return self._store.has_crop_model(name)

    @staticmethod
    def validate_mesh(data: bytes, filename: str, *, collision: bool = False) -> None:
        """Check an uploaded glTF mesh by extension, magic bytes and size.

        Raises:
            SimAssetError: The file is not .glb/.gltf, is not valid glTF, or exceeds 50MB.
        """
        ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
        if ext not in ('glb', 'gltf'):
            raise SimAssetError('only .glb/.gltf files accepted')
        valid = data[:4] == b'glTF' if ext == 'glb' else data.strip()[:1] == b'{'
        if not valid:
            raise SimAssetError('invalid glTF file')
        if len(data) > MAX_MESH_BYTES:
            prefix = 'collision file' if collision else 'file'
            raise SimAssetError(f'{prefix} too large (max 50MB)')

    def create_crop_model(self, name: str, visual: bytes | None,
                          collision: bytes | None = None) -> str:
        """Write a new crop model folder (meshes, model.config, model.sdf) and return its stripped name.

        The collision mesh falls back to the visual one. Mesh contents are not validated
        here; call validate_mesh separately. On write failure, the store attempts to remove
        the model folder, then propagates the original exception.

        Raises:
            SimAssetError: The name is empty, not alphanumeric plus underscore, or already
                exists, or no visual mesh was supplied. OSError propagates from writing.
        """
        name = (name or '').strip()
        if not name:
            raise SimAssetError('enter a model name')
        if not _MODEL_NAME_RE.fullmatch(name):
            raise SimAssetError('name must be alphanumeric + underscore')
        if self._store.name_taken(name):
            raise SimAssetError(f'model "{name}" already exists')
        if visual is None:
            raise SimAssetError('upload a visual mesh first')

        visual_name = f'{name}.glb'
        collision_name = f'{name}_collision.glb' if collision is not None else visual_name
        meshes = {visual_name: visual}
        if collision is not None:
            meshes[collision_name] = collision
        self._store.write_crop_model(
            name, meshes, _MODEL_CONFIG.format(name=name),
            _MODEL_SDF.format(name=name, collision=collision_name, visual=visual_name))
        return name

    @staticmethod
    def classify_map(filename: str) -> str:
        """Guess the PBR role of a texture file, mirroring Forest3D's keyword matching."""
        lower = filename.lower()
        if any(key in lower for key in ('diff', 'albedo', 'base', 'color')):
            return 'albedo'
        if any(key in lower for key in ('normal', 'nor', 'nrm')):
            return 'normal'
        if 'rough' in lower:
            return 'roughness'
        return 'other'

    def import_soil_zip(self, data: bytes) -> list[str]:
        """Replace the imported soil set with the image maps in a zip and return their names.

        Folder structure is flattened to base names; later entries overwrite earlier files
        with the same base name. Returned names follow archive order and may include duplicates.
        Extraction and filesystem errors propagate and may leave a partial replacement
        after the old textures have been removed. Blocking; call from a worker thread.

        Raises:
            SimAssetError: The data is not a zip or holds no .jpg/.jpeg/.png maps. The
                existing soil set is left alone.
        """
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise SimAssetError('not a valid .zip file') from exc
        with archive:
            members = [m for m in archive.namelist()
                       if not m.endswith('/') and Path(m).suffix.lower() in SOIL_MAP_SUFFIXES]
            if not members:
                raise SimAssetError(
                    'no .jpg/.png maps found in the zip '
                    '(textures may be .exr — convert first)')
            self._store.clear_soil_textures()
            names = []
            for member in members:
                name = Path(member).name
                with archive.open(member) as src, self._store.open_soil_texture(name) as dest:
                    shutil.copyfileobj(src, dest)
                names.append(name)
        return names
