from nicegui import run as ng_run
from nicegui import ui

from devkit_ui.pages.sim.uploads import read_upload
from devkit_ui.view_models.sim_view_model import SimAssetError, SimViewModel

_OK = 'color:#1a7f37'
_ERROR = 'color:#cf222e'


class SoilTextureCard(ui.card):
    """Import a zipped soil asset folder for the next world rebuild.

    The image maps are harvested into /workspace/uploads (persisted across image rebuilds) and
    staged into the ground model on the next world rebuild, where Forest3D turns them into a PBR
    material.
    """

    def __init__(self, sim_vm: SimViewModel) -> None:
        """Build the upload control.

        Parameters:
            sim_vm (SimViewModel): Imports the zip and classifies its maps.
        """
        super().__init__()
        self._sim_vm = sim_vm
        with self:
            ui.label('Soil Texture').classes('font-semibold mb-2')
            self._status = ui.label('').classes('text-xs font-mono').style('color:#57606a')
            ui.upload(
                label='Import soil asset (.zip)', auto_upload=True, on_upload=self._import_zip,
            ).props('accept=.zip').classes('max-w-md')

    def _show(self, text: str, ok: bool = False) -> None:
        """Show a status message, green when ok and red otherwise."""
        self._status.set_text(text)
        self._status.style(_OK if ok else _ERROR)

    async def _import_zip(self, event) -> None:
        """Import the uploaded zip off the UI loop and summarise the maps it contained."""
        try:
            data, _ = await read_upload(event, 'soil.zip')
            names = await ng_run.io_bound(self._sim_vm.import_soil_zip, data)
        except SimAssetError as exc:
            self._show(str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'import failed: {exc}')
            return
        if names is None:
            self._show('import cancelled')
            return
        summary = ', '.join(f'{self._sim_vm.classify_map(n)}={n}' for n in names)
        self._show(f'imported {len(names)} map(s) — {summary}. Rebuild World to apply.', ok=True)
