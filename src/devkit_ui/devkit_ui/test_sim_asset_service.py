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

    def test_size_limit_is_inclusive_for_both_mesh_roles_and_formats(self) -> None:
        """Accept exactly 50 MiB and reject the very next byte for either role."""
        for filename, header in [('crop.glb', b'glTF'), ('crop.gltf', b'{"asset":{}}')]:
            data = header.ljust(MAX_MESH_BYTES, b' ')
            for collision in (False, True):
                with self.subTest(filename=filename, collision=collision):
                    self.service.validate_mesh(data, filename, collision=collision)
                    prefix = 'collision file' if collision else 'file'
                    with self.assertRaisesRegex(SimAssetError, rf'^{prefix} too large \(max 50MB\)$'):
                        self.service.validate_mesh(data + b' ', filename, collision=collision)

    def test_accepts_whitespace_padded_json_objects_and_arrays(self) -> None:
        """The JSON signature check supports both roots and mixed-case suffixes."""
        for data in (b'\t\r\n {"asset": {}} \n', b' \n [] \t'):
            with self.subTest(data=data):
                self.service.validate_mesh(data, 'crop.mesh.GlTf')

    def test_rejects_missing_or_disguised_extensions(self) -> None:
        """A valid signature cannot bypass the uploaded filename restriction."""
        for filename in ('crop', '', 'crop.', 'crop.glb.exe', 'crop.gltf ', 'crop.GLB/texture'):
            with self.subTest(filename=filename):
                with self.assertRaisesRegex(SimAssetError, r'^only \.glb/\.gltf files accepted$'):
                    self.service.validate_mesh(GLB, filename)

    def test_rejects_empty_truncated_and_mismatched_signatures(self) -> None:
        """Choose signature validation by extension without accepting empty content."""
        cases = [
            ('crop.glb', b''), ('crop.glb', b'g'), ('crop.glb', b'glT'),
            ('crop.glb', b'GLTF'), ('crop.glb', b' glTF'), ('crop.glb', b'{}'),
            ('crop.gltf', b''), ('crop.gltf', b' \n\t'), ('crop.gltf', GLB),
        ]
        for filename, data in cases:
            with self.subTest(filename=filename, data=data):
                with self.assertRaisesRegex(SimAssetError, '^invalid glTF file$'):
                    self.service.validate_mesh(data, filename)


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

    def test_lists_only_complete_model_directories_in_sorted_order(self) -> None:
        """Discovery ignores ordinary files and incomplete uploads."""
        for name in ('zucchini', 'beet', 'maize'):
            self.service.create_crop_model(name, GLB)
        (self.crop_dir / 'incomplete').mkdir()
        (self.crop_dir / 'ordinary_file').write_bytes(GLB)

        self.assertEqual(self.service.list_crop_models(), ['beet', 'maize', 'zucchini'])

    def test_has_crop_model_requires_a_directory_and_an_sdf_file(self) -> None:
        """The dropdown fallback and a directory named model.sdf are not models."""
        self.assertFalse(self.service.has_crop_model('plant'))
        (self.crop_dir / 'incomplete').mkdir(parents=True)
        (self.crop_dir / 'sdf_directory' / 'model.sdf').mkdir(parents=True)
        (self.crop_dir / 'ordinary_file').write_bytes(GLB)
        for name in ('missing', 'incomplete', 'sdf_directory', 'ordinary_file'):
            with self.subTest(name=name):
                self.assertFalse(self.service.has_crop_model(name))


class TestSoilImport(SimAssetTestCase):
    """Soil zips replace the previous texture set."""

    def test_classify_map(self) -> None:
        """Verify file names map to PBR roles the way Forest3D does it."""
        cases = {'Soil_Diffuse.jpg': 'albedo', 'x_normal.png': 'normal',
                 'x_Rough.png': 'roughness', 'height.png': 'other'}
        for name, role in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.service.classify_map(name), role)

    def test_classifies_all_aliases_case_insensitively(self) -> None:
        """Recognize all supported Forest3D PBR naming conventions."""
        aliases = {
            'albedo': ('diff', 'albedo', 'base', 'color'),
            'normal': ('normal', 'nor', 'nrm'),
            'roughness': ('rough',),
            'other': ('height', 'ao', 'displacement', 'metallic'),
        }
        for role, keywords in aliases.items():
            for keyword in keywords:
                with self.subTest(keyword=keyword):
                    self.assertEqual(self.service.classify_map(f'SOIL_{keyword.upper()}.JPEG'), role)

    def test_classification_prioritizes_albedo_then_normal_then_roughness(self) -> None:
        """Ambiguous filenames retain the same role as Forest3D's first match."""
        cases = {'rough_normal_color.png': 'albedo', 'rough_nrm.jpg': 'normal', '': 'other'}
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
