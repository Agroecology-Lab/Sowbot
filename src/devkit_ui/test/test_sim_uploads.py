"""Upload event compatibility and simulation asset form state transitions."""

# pylint: disable=protected-access
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from devkit_ui.view_models.sim_view_model import SimAssetError
from test_run_ui import fake_ui, nicegui

with (
    patch.dict(sys.modules, {'nicegui': nicegui}),
    patch.object(nicegui, 'run', SimpleNamespace(), create=True),
):
    # pylint: disable-next=ungrouped-imports  # UI imports require the NiceGUI patch.
    from devkit_ui.pages.sim import plant_placement_card, soil_texture_card
    from devkit_ui.pages.sim.uploads import read_upload


class TestReadUpload(unittest.IsolatedAsyncioTestCase):
    async def test_current_event_awaits_file_and_prefers_it_over_legacy_content(self):
        file = SimpleNamespace(read=AsyncMock(return_value=b'mesh'), name='crop.glb')
        legacy = Mock()
        self.assertEqual(
            await read_upload(SimpleNamespace(file=file, content=legacy), 'default.glb'),
            (b'mesh', 'crop.glb'),
        )
        file.read.assert_awaited_once_with()
        legacy.read.assert_not_called()

    async def test_legacy_event_reads_bytes_and_preserves_name(self):
        event = SimpleNamespace(content=io.BytesIO(b'old mesh'), name='old.gltf')
        self.assertEqual(await read_upload(event, 'default.glb'), (b'old mesh', 'old.gltf'))

    async def test_default_name_is_used_only_when_name_attribute_is_absent(self):
        for modern in (True, False):
            for name in ('absent', '', None):
                with self.subTest(modern=modern, name=name):
                    source = SimpleNamespace(
                        read=AsyncMock(return_value=b'') if modern else Mock(return_value=b'')
                    )
                    event = (
                        SimpleNamespace(file=source) if modern else SimpleNamespace(content=source)
                    )
                    if name != 'absent':
                        target = source if modern else event
                        target.name = name
                    self.assertEqual(
                        await read_upload(event, 'fallback.zip'),
                        (b'', 'fallback.zip' if name == 'absent' else name),
                    )

    async def test_read_failures_propagate_for_both_event_shapes(self):
        for modern in (True, False):
            with self.subTest(modern=modern):
                error = OSError('read failed')
                reader = AsyncMock(side_effect=error) if modern else Mock(side_effect=error)
                event = (
                    SimpleNamespace(file=SimpleNamespace(read=reader))
                    if modern
                    else SimpleNamespace(content=SimpleNamespace(read=reader))
                )
                with self.assertRaises(OSError) as caught:
                    await read_upload(event, 'fallback')
                self.assertIs(caught.exception, error)


class TestPlantUploads(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        for kind in ('select', 'upload'):
            self.enterContext(
                patch.object(
                    fake_ui,
                    kind,
                    side_effect=lambda *a, _kind=kind, **kw: fake_ui._element(_kind, *a, **kw),
                    create=True,
                )
            )
        self.vm = Mock()
        self.vm.list_crop_models.return_value = ['plant']
        self.card = plant_placement_card.PlantPlacementCard(self.vm)
        self.read = self.enterContext(
            patch.object(plant_placement_card, 'read_upload', new_callable=AsyncMock)
        )
        self.read.return_value = (b'glTFmesh', 'maize.glb')

    async def test_visual_and_collision_uploads_validate_and_store_separate_meshes(self):
        await self.card._on_visual('visual-event')
        self.read.assert_awaited_once_with('visual-event', 'visual.glb')
        self.vm.validate_mesh.assert_called_once_with(b'glTFmesh', 'maize.glb')
        self.assertEqual(self.card._visual, {'name': 'maize.glb', 'data': b'glTFmesh'})
        self.assertEqual(self.card._collision, {'name': None, 'data': None})
        self.read.return_value = (b'collision', 'hull.glb')
        await self.card._on_collision('collision-event')
        self.read.assert_awaited_with('collision-event', 'collision.glb')
        self.vm.validate_mesh.assert_called_with(b'collision', 'hull.glb', collision=True)
        self.assertEqual(self.card._collision, {'name': 'hull.glb', 'data': b'collision'})
        self.assertIn('visual: maize.glb, collision: hull.glb', self.card._upload_status.text)
        self.assertEqual(self.card._upload_status.styles[-1], 'color:#1a7f37')

    async def test_invalid_replacement_keeps_last_valid_mesh(self):
        for kind in ('visual', 'collision'):
            with self.subTest(kind=kind):
                stored = getattr(self.card, f'_{kind}')
                stored.update(name='valid.glb', data=b'valid')
                self.vm.validate_mesh.side_effect = SimAssetError('invalid glTF')
                await getattr(self.card, f'_on_{kind}')(object())
                self.assertEqual(stored, {'name': 'valid.glb', 'data': b'valid'})
                self.assertEqual(self.card._upload_status.text, 'invalid glTF')
                self.assertEqual(self.card._upload_status.styles[-1], 'color:#cf222e')

    async def test_read_failure_does_not_validate_or_store_upload(self):
        self.read.side_effect = OSError('disconnected')
        for kind, message in (
            ('visual', 'upload failed: disconnected'),
            ('collision', 'collision upload failed: disconnected'),
        ):
            with self.subTest(kind=kind):
                await getattr(self.card, f'_on_{kind}')(object())
                self.assertEqual(getattr(self.card, f'_{kind}'), {'name': None, 'data': None})
                self.assertEqual(self.card._upload_status.text, message)
        self.vm.validate_mesh.assert_not_called()

    async def test_create_selects_new_model_and_clears_upload_state(self):
        self.card._model_name.value = ' maize '
        self.card._visual.update(name='visual.glb', data=b'visual')
        self.card._collision.update(name='hull.glb', data=b'hull')
        self.vm.create_crop_model.return_value = 'maize'
        self.vm.list_crop_models.return_value = ['maize', 'plant']
        await self.card._create_model()
        self.vm.create_crop_model.assert_called_once_with(' maize ', b'visual', b'hull')
        self.assertEqual(self.card._model.options, ['maize', 'plant'])
        self.assertEqual(self.card._model.value, 'maize')
        self.assertEqual(self.card._model_name.value, '')
        self.assertEqual(self.card._visual, {'name': None, 'data': None})
        self.assertEqual(self.card._collision, {'name': None, 'data': None})
        self.assertEqual(self.card._upload_status.text, 'model "maize" created')

    async def test_create_failure_preserves_inputs_for_retry(self):
        for error, message in (
            (SimAssetError('name taken'), 'name taken'),
            (OSError('read only'), 'failed: read only'),
        ):
            with self.subTest(error=error):
                self.card._model_name.value = 'maize'
                self.card._visual.update(name='visual.glb', data=b'visual')
                self.vm.create_crop_model.side_effect = error
                self.vm.list_crop_models.reset_mock()
                await self.card._create_model()
                self.assertEqual(self.card._model_name.value, 'maize')
                self.assertEqual(self.card._visual['data'], b'visual')
                self.assertEqual(self.card._model.value, 'plant')
                self.assertEqual(self.card._upload_status.text, message)
                self.vm.list_crop_models.assert_not_called()

    def test_spacing_warning_includes_equality_and_clears_when_spacing_increases(self):
        for width, spacing, warns in (
            (80, 80, True),
            (81, 80, True),
            (79, 80, False),
            (None, None, False),
        ):
            with self.subTest(width=width, spacing=spacing):
                self.card._row_width.value = width
                self.card._spacing.value = spacing
                self.card._check_spacing()
                self.assertEqual(
                    self.card._spacing_warning.text,
                    'Warning: row width >= plant spacing' if warns else '',
                )


class TestSoilUpload(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake_ui.reset()
        self.addCleanup(fake_ui.reset)
        self.enterContext(
            patch.object(
                fake_ui,
                'upload',
                side_effect=lambda **kw: fake_ui._element('upload', **kw),
                create=True,
            )
        )
        self.vm = Mock()
        self.card = soil_texture_card.SoilTextureCard(self.vm)
        self.read = self.enterContext(
            patch.object(soil_texture_card, 'read_upload', new_callable=AsyncMock)
        )
        self.read.return_value = (b'zip', 'soil.zip')
        self.io_bound = AsyncMock(return_value=['albedo.png', 'normal.jpg'])
        self.enterContext(
            patch.object(soil_texture_card, 'ng_run', SimpleNamespace(io_bound=self.io_bound))
        )

    async def test_import_runs_off_ui_loop_and_reports_classified_maps(self):
        self.vm.classify_map.side_effect = ['albedo', 'normal']
        await self.card._import_zip('event')
        self.read.assert_awaited_once_with('event', 'soil.zip')
        self.io_bound.assert_awaited_once_with(self.vm.import_soil_zip, b'zip')
        self.assertEqual(
            self.card._status.text,
            'imported 2 map(s) — albedo=albedo.png, normal=normal.jpg. Rebuild World to apply.',
        )
        self.assertEqual(self.card._status.styles[-1], 'color:#1a7f37')

    async def test_cancelled_or_failed_import_does_not_classify_maps(self):
        for error, message in (
            (None, 'import cancelled'),
            (SimAssetError('invalid zip'), 'invalid zip'),
            (OSError('disk full'), 'import failed: disk full'),
        ):
            with self.subTest(error=error):
                self.io_bound.return_value = None
                self.io_bound.side_effect = error
                await self.card._import_zip(object())
                self.assertEqual(self.card._status.text, message)
                self.assertEqual(self.card._status.styles[-1], 'color:#cf222e')
                self.vm.classify_map.assert_not_called()

    async def test_unreadable_upload_never_starts_import(self):
        self.read.side_effect = OSError('closed stream')
        await self.card._import_zip(object())
        self.io_bound.assert_not_awaited()
        self.assertEqual(self.card._status.text, 'import failed: closed stream')


if __name__ == '__main__':
    unittest.main()
