"""Tests for SimAssetService against temporary directories."""
import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from devkit_ui.application_services.sim_asset_service import (
    MAX_MESH_BYTES,
    SimAssetError,
    SimAssetService,
)

GLB = b'glTF' + b'\x00' * 12


def make_zip(files: dict[str, bytes]) -> bytes:
    """Return the bytes of a zip holding files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class SimAssetTestCase(unittest.TestCase):
    """Gives each test a service writing under its own temporary directory."""

    def setUp(self) -> None:
        """Create the temporary directories and the service that uses them."""
        # NOTE: enterContext owns cleanup.
        # pylint: disable-next=consider-using-with
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.crop_dir = self.root / 'models' / 'crop'
        self.soil_dir = self.root / 'soil' / 'textures'
        self.service = SimAssetService(self.crop_dir, self.soil_dir)


class TestValidateMesh(SimAssetTestCase):
    """Uploaded meshes are checked by extension, magic bytes and size."""

    def test_accepts_glb_and_gltf(self) -> None:
        """Verify valid binary and JSON glTF files pass."""
        self.service.validate_mesh(GLB, 'tree.GLB')
        self.service.validate_mesh(b'  {"asset": {}}', 'tree.gltf')

    def test_rejections_use_the_ui_messages(self) -> None:
        """Verify each rejection raises the message shown in the upload label."""
        cases = [
            ((GLB, 'tree.obj', False), 'only .glb/.gltf files accepted'),
            ((b'not gltf', 'tree.glb', False), 'invalid glTF file'),
            ((b'plain text', 'tree.gltf', False), 'invalid glTF file'),
            ((GLB + bytes(MAX_MESH_BYTES), 'tree.glb', False), r'^file too large \(max 50MB\)$'),
            ((GLB + bytes(MAX_MESH_BYTES), 'tree.glb', True),
             r'^collision file too large \(max 50MB\)$'),
        ]
        for (data, name, collision), message in cases:
            with self.subTest(message=message, collision=collision):
                with self.assertRaisesRegex(SimAssetError, message):
                    self.service.validate_mesh(data, name, collision=collision)


class TestCropModels(SimAssetTestCase):
    """Crop models are written as a folder Gazebo can load."""

    def test_creates_model_files(self) -> None:
        """Verify meshes, model.config and model.sdf are written and the model is listed."""
        name = self.service.create_crop_model(' maize_1 ', GLB, b'glTFcollision')

        model_dir = self.crop_dir / 'maize_1'
        self.assertEqual(name, 'maize_1')
        self.assertEqual((model_dir / 'mesh' / 'maize_1.glb').read_bytes(), GLB)
        self.assertEqual((model_dir / 'mesh' / 'maize_1_collision.glb').read_bytes(),
                         b'glTFcollision')
        self.assertIn('<name>maize_1</name>', (model_dir / 'model.config').read_text())
        sdf = (model_dir / 'model.sdf').read_text()
        self.assertIn('<uri>mesh/maize_1_collision.glb</uri>', sdf)
        self.assertIn('<uri>mesh/maize_1.glb</uri>', sdf)
        self.assertTrue(self.service.has_crop_model('maize_1'))
        self.assertEqual(self.service.list_crop_models(), ['maize_1'])

    def test_collision_falls_back_to_visual_mesh(self) -> None:
        """Verify without a collision mesh the model reuses the visual one."""
        self.service.create_crop_model('beet', GLB)

        self.assertEqual([p.name for p in (self.crop_dir / 'beet' / 'mesh').iterdir()],
                         ['beet.glb'])
        self.assertEqual((self.crop_dir / 'beet' / 'model.sdf').read_text().count(
            'mesh/beet.glb'), 2)

    def test_rejections_use_the_ui_messages(self) -> None:
        """Verify bad requests raise their messages and leave nothing on disk."""
        self.service.create_crop_model('taken', GLB)
        cases = [
            (('  ', GLB), 'enter a model name'),
            (('bad name', GLB), 'name must be alphanumeric'),
            (('../escape', GLB), 'name must be alphanumeric'),
            (('taken', GLB), 'model "taken" already exists'),
            (('fresh', None), 'upload a visual mesh first'),
        ]
        for args, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SimAssetError, message):
                    self.service.create_crop_model(*args)
        self.assertEqual(sorted(p.name for p in self.crop_dir.iterdir()), ['taken'])

    def test_failed_write_removes_partial_model(self) -> None:
        """Verify a write that fails part way leaves no folder blocking a retry."""
        blocked = self.crop_dir / 'blocked'
        with self.assertRaises(TypeError):
            self.service.create_crop_model('blocked', 'not bytes')  # type: ignore[arg-type]
        self.assertFalse(blocked.exists())
        self.assertFalse(self.service.has_crop_model('blocked'))

    def test_lists_default_when_no_models(self) -> None:
        """Verify an empty or missing directory lists the built-in plant model."""
        self.assertEqual(self.service.list_crop_models(), ['plant'])
        (self.crop_dir / 'no_sdf').mkdir(parents=True)
        self.assertEqual(self.service.list_crop_models(), ['plant'])


class TestSoilImport(SimAssetTestCase):
    """Soil zips replace the previous texture set."""

    def test_classify_map(self) -> None:
        """Verify file names map to PBR roles the way Forest3D does it."""
        cases = {'Soil_Diffuse.jpg': 'albedo', 'x_normal.png': 'normal',
                 'x_Rough.png': 'roughness', 'height.png': 'other'}
        for name, role in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.service.classify_map(name), role)

    def test_imports_flattened_image_maps_only(self) -> None:
        """Verify image maps are extracted by base name and other files are skipped."""
        data = make_zip({'soil/Color.jpg': b'a', 'soil/sub/Normal.PNG': b'b',
                         'soil/height.exr': b'c', 'soil/readme.txt': b'd', 'soil/': b''})

        names = self.service.import_soil_zip(data)

        self.assertEqual(sorted(names), ['Color.jpg', 'Normal.PNG'])
        self.assertEqual(sorted(p.name for p in self.soil_dir.iterdir()),
                         ['Color.jpg', 'Normal.PNG'])

    def test_new_import_replaces_previous_set(self) -> None:
        """Verify a second import removes the first set's files."""
        self.service.import_soil_zip(make_zip({'old_color.jpg': b'a'}))
        self.service.import_soil_zip(make_zip({'new_color.jpg': b'b'}))

        self.assertEqual([p.name for p in self.soil_dir.iterdir()], ['new_color.jpg'])

    def test_rejections_keep_existing_set(self) -> None:
        """Verify an invalid zip or one without usable maps leaves the current set alone."""
        self.service.import_soil_zip(make_zip({'keep.jpg': b'a'}))
        cases = [(b'not a zip', 'not a valid .zip file'),
                 (make_zip({'height.exr': b'x'}), r'no \.jpg/\.png maps found')]
        for data, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SimAssetError, message):
                    self.service.import_soil_zip(data)
        self.assertEqual([p.name for p in self.soil_dir.iterdir()], ['keep.jpg'])


if __name__ == '__main__':
    unittest.main()
