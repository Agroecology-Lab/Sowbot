"""Crop-model persistence and recovery regressions for SimAssetService."""
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from devkit_ui.application_services.sim_asset_service import SimAssetError
from test_sim_asset_service import GLB, SimAssetTestCase


class TestCropModelPersistence(SimAssetTestCase):
    """Check loadable model artifacts and protect existing uploads."""

    def test_xml_references_the_correct_mesh_for_each_role(self) -> None:
        """Gazebo metadata must resolve to the uploaded bytes with unit scale."""
        for collision in (None, b'glTFcollision'):
            name = 'shared_mesh' if collision is None else 'separate_mesh'
            with self.subTest(collision=collision):
                self.service.create_crop_model(name, GLB, collision)
                model_dir = self.crop_dir / name
                config = ET.parse(model_dir / 'model.config').getroot()
                self.assertEqual(config.tag, 'model')
                self.assertEqual(config.findtext('name'), name)
                self.assertEqual(config.findtext('version'), '1.0')
                sdf_reference = config.find('sdf')
                self.assertIsNotNone(sdf_reference)
                sdf = ET.parse(model_dir / sdf_reference.text).getroot()
                self.assertEqual(sdf.tag, 'sdf')
                self.assertEqual(sdf.get('version'), sdf_reference.get('version'))
                self.assertEqual(sdf.find('model').get('name'), name)
                self.assertEqual(sdf.findtext('model/static'), 'true')

                for role, expected in [('visual', GLB), ('collision', collision or GLB)]:
                    mesh = sdf.find(f'model/link/{role}/geometry/mesh')
                    self.assertIsNotNone(mesh)
                    self.assertEqual(mesh.findtext('scale'), '1 1 1')
                    self.assertEqual((model_dir / mesh.findtext('uri')).read_bytes(), expected)

    def test_rejected_names_never_create_directories(self) -> None:
        """Names cannot escape the crop directory or inject XML into metadata."""
        names = ('', '\t\n', '/absolute', '.', '..', '../escape', 'a/b', 'a\\b',
                 'a-b', 'a.b', 'a\nb', 'maïs', '<model>', 'a"b', 'a\x00b')
        for name in names:
            with self.subTest(name=name):
                with self.assertRaises(SimAssetError):
                    self.service.create_crop_model(name, GLB)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_visual_never_creates_directories(self) -> None:
        """A collision upload alone cannot produce an incomplete model."""
        with self.assertRaisesRegex(SimAssetError, '^upload a visual mesh first$'):
            self.service.create_crop_model('maize', None, GLB)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_file_or_partial_folder_is_preserved(self) -> None:
        """Rejected duplicate names cannot destroy pre-existing user data."""
        self.crop_dir.mkdir(parents=True)
        existing_file = self.crop_dir / 'file'
        existing_file.write_bytes(b'keep file')
        partial = self.crop_dir / 'partial'
        partial.mkdir()
        (partial / 'keep.txt').write_bytes(b'keep partial model')
        self.service.create_crop_model('complete', GLB)
        before = self._crop_files()

        for name in ('file', 'partial', ' complete '):
            with self.subTest(name=name):
                with self.assertRaisesRegex(SimAssetError, 'already exists'):
                    self.service.create_crop_model(name, b'glTFreplacement')
                self.assertEqual(self._crop_files(), before)

    def test_each_write_failure_cleans_up_and_allows_retry(self) -> None:
        """Partial writes at every artifact preserve neighbors and allow the same name."""
        self.service.create_crop_model('neighbor', GLB)
        neighbor_files = self._crop_files()
        failures = [
            ('write_bytes', 'mesh/maize.glb'),
            ('write_bytes', 'mesh/maize_collision.glb'),
            ('write_text', 'model.config'),
            ('write_text', 'model.sdf'),
        ]
        for index, (method, template_path) in enumerate(failures):
            name = f'maize_{index}'
            relative_path = template_path.replace('maize', name)
            failed_path = self.crop_dir / name / relative_path
            original_write = getattr(Path, method)
            error = OSError('disk full')

            def fail_after_write(path, *args, write=original_write, target=failed_path,
                                 failure=error, **kwargs):
                result = write(path, *args, **kwargs)
                if path == target:
                    raise failure
                return result

            with self.subTest(artifact=relative_path):
                with patch.object(Path, method, autospec=True, side_effect=fail_after_write):
                    with self.assertRaises(OSError) as raised:
                        self.service.create_crop_model(name, GLB, b'glTFcollision')
                self.assertIs(raised.exception, error)
                self.assertFalse((self.crop_dir / name).exists())
                self.assertFalse(self.service.has_crop_model(name))
                self.assertEqual(self._crop_files(), neighbor_files)

                self.assertEqual(
                    self.service.create_crop_model(name, GLB, b'glTFcollision'), name)
                self.assertTrue(self.service.has_crop_model(name))
                neighbor_files = self._crop_files()

    def test_directory_creation_failure_propagates_and_can_be_retried(self) -> None:
        """Failure before a mesh is written also leaves the name available."""
        error = PermissionError('read-only directory')
        with patch.object(Path, 'mkdir', side_effect=error):
            with self.assertRaises(PermissionError) as raised:
                self.service.create_crop_model('maize', GLB)
        self.assertIs(raised.exception, error)
        self.assertFalse((self.crop_dir / 'maize').exists())
        self.assertEqual(self.service.create_crop_model('maize', GLB), 'maize')

    def test_interrupted_write_removes_partial_model(self) -> None:
        """An interruption must not leave a name blocked by a half-written model."""
        with patch.object(Path, 'write_text', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.create_crop_model('maize', GLB)
        self.assertFalse((self.crop_dir / 'maize').exists())
        self.assertEqual(self.service.create_crop_model('maize', GLB), 'maize')

    def _crop_files(self) -> dict[Path, bytes]:
        return {path.relative_to(self.crop_dir): path.read_bytes()
                for path in self.crop_dir.rglob('*') if path.is_file()}


if __name__ == '__main__':
    unittest.main()
