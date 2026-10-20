"""Tests for SimAssetStore against temporary directories."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from devkit_ui.sim_asset_store import SimAssetStore


class TestSimAssetStore(unittest.TestCase):
    """The store reads and writes only the two directories it is given."""

    def setUp(self) -> None:
        """Create the temporary directories and the store that uses them."""
        # NOTE: enterContext owns cleanup.
        # pylint: disable-next=consider-using-with
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.crop_dir = self.root / 'models' / 'crop'
        self.soil_dir = self.root / 'soil' / 'textures'
        self.store = SimAssetStore(self.crop_dir, self.soil_dir)

    def test_missing_crop_directory_has_no_models(self) -> None:
        """An unwritten crop directory lists nothing and holds no names."""
        self.assertEqual(self.store.crop_model_names(), [])
        self.assertFalse(self.store.name_taken('maize'))
        self.assertFalse(self.store.has_crop_model('maize'))

    def test_written_model_is_listed_sorted_and_taken(self) -> None:
        """A written model needs its model.sdf to count as a model; any entry takes the name."""
        for name in ('wheat', 'maize'):
            self.store.write_crop_model(name, {f'{name}.glb': b'glTF'}, '<model/>', '<sdf/>')
        (self.crop_dir / 'no_sdf').mkdir()
        (self.crop_dir / 'file').write_bytes(b'x')

        self.assertEqual(self.store.crop_model_names(), ['maize', 'wheat'])
        self.assertTrue(self.store.has_crop_model('maize'))
        self.assertFalse(self.store.has_crop_model('no_sdf'))
        self.assertTrue(self.store.name_taken('no_sdf'))
        self.assertTrue(self.store.name_taken('file'))
        self.assertEqual((self.crop_dir / 'maize' / 'mesh' / 'maize.glb').read_bytes(), b'glTF')
        self.assertEqual((self.crop_dir / 'maize' / 'model.config').read_text(), '<model/>')
        self.assertEqual((self.crop_dir / 'maize' / 'model.sdf').read_text(), '<sdf/>')

    def test_clear_soil_textures_replaces_the_folder_contents(self) -> None:
        """Clearing removes old textures and leaves an empty folder to write into."""
        self.soil_dir.mkdir(parents=True)
        (self.soil_dir / 'old.png').write_bytes(b'old')

        self.store.clear_soil_textures()
        with self.store.open_soil_texture('new.png') as dest:
            dest.write(b'new')

        self.assertEqual([p.name for p in self.soil_dir.iterdir()], ['new.png'])
        self.assertEqual((self.soil_dir / 'new.png').read_bytes(), b'new')


if __name__ == '__main__':
    unittest.main()
