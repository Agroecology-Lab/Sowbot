"""Create and import the files the Gazebo world is built from (crop models, soil textures).

This is the file-only half of the Sim page's controls: it validates
uploads and writes them under /workspace. Launching and stopping processes is separate.
No ROS or NiceGUI imports.
"""
from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path

CROP_MODELS_DIR = Path('/workspace/models/crop')
SOIL_TEXTURES_DIR = Path('/workspace/uploads/soil_custom/textures')
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

    def __init__(self, crop_models_dir: Path = CROP_MODELS_DIR,
                 soil_textures_dir: Path = SOIL_TEXTURES_DIR) -> None:
        """Keep the directories crop models and soil textures are written to."""
        self._crop_dir = crop_models_dir
        self._soil_dir = soil_textures_dir

    def list_crop_models(self) -> list[str]:
        """Return the crop model folders that have a model.sdf, or ['plant'] if none."""
        models = []
        if self._crop_dir.exists():
            models = [d.name for d in sorted(self._crop_dir.iterdir())
                      if d.is_dir() and (d / 'model.sdf').exists()]
        return models or ['plant']

    def has_crop_model(self, name: str) -> bool:
        """Return whether name is a crop model folder containing a model.sdf."""
        model_dir = self._crop_dir / name
        return model_dir.is_dir() and (model_dir / 'model.sdf').is_file()

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
        """Write a new crop model folder (meshes, model.config, model.sdf) and return its name.

        The collision mesh falls back to the visual one. A model that fails part way
        through is removed, so it does not block creating it again.

        Raises:
            SimAssetError: The name is empty, not alphanumeric plus underscore, or already
                exists, or no visual mesh was supplied. OSError propagates from writing.
        """
        name = (name or '').strip()
        if not name:
            raise SimAssetError('enter a model name')
        if not _MODEL_NAME_RE.fullmatch(name):
            raise SimAssetError('name must be alphanumeric + underscore')
        model_dir = self._crop_dir / name
        if model_dir.exists():
            raise SimAssetError(f'model "{name}" already exists')
        if visual is None:
            raise SimAssetError('upload a visual mesh first')

        visual_name = f'{name}.glb'
        collision_name = f'{name}_collision.glb' if collision is not None else visual_name
        try:
            mesh_dir = model_dir / 'mesh'
            mesh_dir.mkdir(parents=True)
            (mesh_dir / visual_name).write_bytes(visual)
            if collision is not None:
                (mesh_dir / collision_name).write_bytes(collision)
            (model_dir / 'model.config').write_text(
                _MODEL_CONFIG.format(name=name), encoding='utf-8')
            (model_dir / 'model.sdf').write_text(
                _MODEL_SDF.format(name=name, collision=collision_name, visual=visual_name),
                encoding='utf-8')
        except BaseException:
            shutil.rmtree(model_dir, ignore_errors=True)
            raise
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

        Folder structure is flattened to base names. Blocking; call from a worker thread.

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
            if self._soil_dir.exists():
                shutil.rmtree(self._soil_dir)
            self._soil_dir.mkdir(parents=True, exist_ok=True)
            names = []
            for member in members:
                out = self._soil_dir / Path(member).name
                with archive.open(member) as src, open(out, 'wb') as dest:
                    shutil.copyfileobj(src, dest)
                names.append(out.name)
        return names
