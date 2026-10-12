"""Soil texture import boundaries and filesystem preservation regressions."""
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from devkit_ui.application_services.sim_asset_service import SimAssetError
from devkit_ui.test_sim_asset_service import SimAssetTestCase, make_zip


class TestSoilImportRegressions(SimAssetTestCase):
    """Import only supported image members into the configured texture directory."""

    def test_supported_suffixes_preserve_bytes_and_flatten_nested_names(self) -> None:
        """JPEG aliases and mixed-case extensions retain their exact binary content."""
        files = {
            'vendor/soil/color.JpG': b'\xff\xd8\x00albedo',
            'vendor/normal.jPeG': b'\xff\xd8\x00normal',
            'rough.PnG': b'\x89PNG\x00rough',
        }
        excluded = {'height.EXR': b'exr', 'preview.gif': b'gif', 'readme.png.txt': b'text',
                    'looks_like_image.png/': b'', 'no_suffix': b'other'}

        names = self.service.import_soil_zip(make_zip(files | excluded))

        expected = {Path(name).name: data for name, data in files.items()}
        self.assertCountEqual(names, expected)
        self.assertEqual({p.name: p.read_bytes() for p in self.soil_dir.iterdir()}, expected)

    def test_archive_paths_are_flattened_without_writing_outside_texture_directory(self) -> None:
        """Absolute paths and parent components cannot overwrite neighboring files."""
        sentinel = self.root / 'sentinel.png'
        sentinel.write_bytes(b'keep outside')
        files = {
            '../../sentinel.png': b'parent image',
            str(self.root / 'absolute.jpg'): b'absolute image',
            'nested/../normal.jpeg': b'normal image',
        }

        names = self.service.import_soil_zip(make_zip(files))

        self.assertCountEqual(names, ['sentinel.png', 'absolute.jpg', 'normal.jpeg'])
        self.assertEqual(sentinel.read_bytes(), b'keep outside')
        self.assertFalse((self.root / 'absolute.jpg').exists())
        self.assertEqual(
            {p.relative_to(self.root) for p in self.root.rglob('*') if p.is_file()},
            {Path('sentinel.png')} | {Path('soil/textures') / name for name in names})
        for name, data in files.items():
            self.assertEqual((self.soil_dir / Path(name).name).read_bytes(), data)

    def test_replacement_removes_nested_old_textures_but_preserves_siblings(self) -> None:
        """Replacing a set clears stale subfolders without deleting adjacent assets."""
        old_dir = self.soil_dir / 'old' / 'nested'
        old_dir.mkdir(parents=True)
        (old_dir / 'old.png').write_bytes(b'old')
        sibling = self.soil_dir.parent / 'metadata.txt'
        sibling.write_bytes(b'keep metadata')

        self.assertEqual(self.service.import_soil_zip(make_zip({'new.jpg': b'new'})), ['new.jpg'])

        self.assertEqual(list(self.soil_dir.iterdir()), [self.soil_dir / 'new.jpg'])
        self.assertEqual((self.soil_dir / 'new.jpg').read_bytes(), b'new')
        self.assertEqual(sibling.read_bytes(), b'keep metadata')

    def test_rejected_archives_preserve_existing_bytes_and_directories(self) -> None:
        """Invalid, empty and image-free archives leave the previous set intact."""
        nested = self.soil_dir / 'nested'
        nested.mkdir(parents=True)
        keep = nested / 'keep.png'
        keep.write_bytes(b'\x89PNG\x00keep')
        cases = [b'', b'PK\x03\x04truncated', make_zip({}),
                 make_zip({'folder.png/': b'', 'height.EXR': b'exr'})]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(SimAssetError):
                    self.service.import_soil_zip(data)
                self.assertEqual(set(self.soil_dir.rglob('*')), {nested, keep})
                self.assertEqual(keep.read_bytes(), b'\x89PNG\x00keep')

    def test_rejected_first_import_does_not_create_texture_directory(self) -> None:
        """Validation precedes all filesystem mutations on the first import too."""
        for data in (b'not a zip', make_zip({}), make_zip({'height.exr': b'x'})):
            with self.subTest(data=data):
                with self.assertRaises(SimAssetError):
                    self.service.import_soil_zip(data)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_zip_retains_parse_error_as_cause(self) -> None:
        """The UI error preserves the ZIP failure for diagnostics."""
        with self.assertRaisesRegex(SimAssetError, r'^not a valid \.zip file$') as raised:
            self.service.import_soil_zip(b'not a zip')
        self.assertIsInstance(raised.exception.__cause__, zipfile.BadZipFile)

    def test_filesystem_error_is_not_reported_as_invalid_upload(self) -> None:
        """Operational failures propagate to the UI's separate failure handler."""
        error = PermissionError('read-only directory')
        with patch.object(Path, 'mkdir', side_effect=error):
            with self.assertRaises(PermissionError) as raised:
                self.service.import_soil_zip(make_zip({'color.jpg': b'color'}))
        self.assertIs(raised.exception, error)


if __name__ == '__main__':
    unittest.main()
