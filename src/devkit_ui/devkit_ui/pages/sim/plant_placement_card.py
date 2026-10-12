from dataclasses import dataclass

from nicegui import ui

from devkit_ui.pages.sim.uploads import read_upload
from devkit_ui.view_models.sim_view_model import SimAssetError, SimViewModel

_OK = 'color:#1a7f37'
_ERROR = 'color:#cf222e'


@dataclass(frozen=True)
class PlantSettings:
    """Plant placement values for a world rebuild, in metres, fractions and percent."""

    spacing_m: float
    row_width_m: float
    scale: float
    category: str
    model: str
    weed_density: int


class PlantPlacementCard(ui.card):
    """Plant spacing, row width, scale, weed density and crop model for the next world rebuild."""

    def __init__(self, sim_vm: SimViewModel) -> None:
        """Build the placement inputs and the new-model upload controls.

        Parameters:
            sim_vm (SimViewModel): Lists, validates and creates crop models.
        """
        super().__init__()
        self._sim_vm = sim_vm
        self._visual: dict = {'name': None, 'data': None}
        self._collision: dict = {'name': None, 'data': None}
        with self:
            ui.label('Plant Placement').classes('font-semibold mb-2')
            self._build_inputs()
            self._build_upload()

    def settings(self) -> PlantSettings:
        """Return placement values with distances in meters and scale as a factor.

        Cleared fields use defaults; zero spacing, width, or scale also uses its default.
        Weed density stays a percentage, and an explicit zero is preserved.
        """
        return PlantSettings(
            spacing_m=float(self._spacing.value or 100) / 100.0,
            row_width_m=float(self._row_width.value or 80) / 100.0,
            scale=float(self._scale.value or 100) / 100.0,
            category=self._category.value or 'all',
            model=self._model.value or 'plant',
            weed_density=(int(self._weed_density.value)
                          if self._weed_density.value is not None else 10),
        )

    def _build_inputs(self) -> None:
        """Build the spacing, width, scale, model and weed density inputs."""
        with ui.row().classes('w-full gap-4 mt-1'):
            with ui.column().classes('flex-1 gap-0'):
                ui.html('<div class="sec-label">Plant Spacing</div>')
                self._spacing = ui.number(
                    value=100, min=10, max=1000, step=5, precision=0, suffix='cm').classes('w-full')
            with ui.column().classes('flex-1 gap-0'):
                ui.html('<div class="sec-label">Row width</div>')
                self._row_width = ui.number(
                    value=80, min=20, max=300, step=5, precision=0, suffix='cm').classes('w-full')

        self._spacing_warning = ui.label('').classes('text-xs').style('color:#9a6700')
        self._row_width.on('update:model-value', lambda _: self._check_spacing())
        self._spacing.on('update:model-value', lambda _: self._check_spacing())

        with ui.row().classes('w-full gap-4 mt-1'):
            with ui.column().classes('flex-1 gap-0'):
                ui.html('<div class="sec-label">Scale</div>')
                with ui.row().classes('items-center gap-1 w-full'):
                    self._scale = ui.number(
                        value=100, min=5, max=1000, step=5, precision=0, suffix='%').classes('flex-1')
                    ui.label('on').classes('text-xs').style('color:#8c959f')
                    self._category = ui.select(
                        options=['all', 'crop', 'weed', 'irrigation'], value='all').classes('w-28')
            with ui.column().classes('flex-1 gap-0'):
                ui.html('<div class="sec-label">Model</div>')
                models = self._sim_vm.list_crop_models()
                self._model = ui.select(options=models, value=models[0]).classes('w-full')

        with ui.row().classes('w-full gap-4 mt-1'):
            with ui.column().classes('flex-1 gap-0'):
                ui.html('<div class="sec-label">Weed Density</div>')
                with ui.row().classes('items-center gap-1 w-full'):
                    self._weed_density = ui.number(
                        value=10, min=0, max=100, step=5, precision=0, suffix='%').classes('flex-1')
                    ui.label('on').classes('text-xs').style('color:#8c959f')

    def _check_spacing(self) -> None:
        """Warn when the row is at least as wide as the plant spacing."""
        row_width = float(self._row_width.value or 80)
        spacing = float(self._spacing.value or 100)
        self._spacing_warning.set_text('Warning: row width >= plant spacing' if row_width >= spacing else '')

    def _build_upload(self) -> None:
        """Build the controls for creating a crop model from uploaded meshes."""
        ui.html('<div class="sec-label mt-2">Upload new model</div>')
        self._upload_status = ui.label('').classes('text-xs font-mono').style('color:#57606a')
        self._model_name = ui.input(label='Model name', placeholder='my_plant').classes('w-40')
        with ui.row().classes('items-center gap-2 flex-wrap'):
            ui.upload(
                label='Visual mesh (.glb/.gltf) *', auto_upload=True, on_upload=self._on_visual,
            ).props('accept=.glb,.gltf').classes('max-w-xs')
            ui.upload(
                label='Collision mesh (optional)', auto_upload=True, on_upload=self._on_collision,
            ).props('accept=.glb,.gltf').classes('max-w-xs')
            ui.button('Create Model', on_click=self._create_model).props(
                'color=primary no-caps').classes('px-4')

    def _show(self, text: str, ok: bool = False) -> None:
        """Show a status message, green when ok and red otherwise."""
        self._upload_status.set_text(text)
        self._upload_status.style(_OK if ok else _ERROR)

    async def _on_visual(self, event) -> None:
        """Validate and keep the uploaded visual mesh."""
        try:
            data, name = await read_upload(event, 'visual.glb')
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'upload failed: {exc}')
            return
        try:
            self._sim_vm.validate_mesh(data, name)
        except SimAssetError as exc:
            self._show(str(exc))
            return
        self._visual.update(name=name, data=data)
        self._show(f'visual: {name} ({len(data) // 1024}KB)', ok=True)

    async def _on_collision(self, event) -> None:
        """Validate and keep the uploaded collision mesh."""
        try:
            data, name = await read_upload(event, 'collision.glb')
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'collision upload failed: {exc}')
            return
        try:
            self._sim_vm.validate_mesh(data, name, collision=True)
        except SimAssetError as exc:
            self._show(str(exc))
            return
        self._collision.update(name=name, data=data)
        self._show(f'visual: {self._visual["name"] or "—"}, collision: {name}', ok=True)

    async def _create_model(self) -> None:
        """Write the crop model from the kept meshes and select it."""
        try:
            name = self._sim_vm.create_crop_model(
                self._model_name.value, self._visual['data'], self._collision['data'])
        except SimAssetError as exc:
            self._show(str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._show(f'failed: {exc}')
            return
        self._model.options = self._sim_vm.list_crop_models()
        self._model.value = name
        self._visual.update(name=None, data=None)
        self._collision.update(name=None, data=None)
        self._model_name.value = ''
        self._show(f'model "{name}" created', ok=True)
