from nicegui import ui

from devkit_ui.view_models.topology_view_model import TopologyViewModel


class MapArchiveCard(ui.card):
    """Save a named copy of the topology map, or archive it and clear the live map.

    Archive & Clear acts on the live map file, the one worldgen.sh builds the simulated world
    from and the one the real robot loads, so it is destructive outside the sim too. It sits on
    the Sim tab because Rebuild World reads that map; the dialog states what it will wipe.
    """

    def __init__(self, topo_vm: TopologyViewModel) -> None:
        """Build the save and archive controls.

        Parameters:
            topo_vm (TopologyViewModel): Saves copies of the live map and archives and clears it.
                Both return a status message; one starting with ERROR is shown as a failure.
        """
        super().__init__()
        self._topo_vm = topo_vm
        with self:
            ui.label('Map Archive').classes('font-semibold mb-2')
            self._status = ui.label('').classes('text-xs font-mono mt-1').style('color:#57606a')
            with ui.row().classes('items-center gap-2 w-full mb-2'):
                self._save_name = ui.input(
                    label='Save copy as', placeholder='e.g. north_field_2026',
                ).props('dense').classes('flex-1')
                ui.button('Save', on_click=self._save).props('outline no-caps')
            ui.button('Archive & Clear Map', on_click=self._archive).props(
                'color=negative outline no-caps').classes('px-4')

    def _show(self, status: str) -> None:
        """Show a status message, red when it reports an error."""
        self._status.set_text(status)
        self._status.style('color:#cf222e' if status.startswith('ERROR') else 'color:#1a7f37')

    def _save(self) -> None:
        """Save a named map copy, show its status, and clear the name on success."""
        status = self._topo_vm.save_map_as(self._save_name.value)
        self._show(status)
        if not status.startswith('ERROR'):
            self._save_name.set_value('')

    async def _archive(self) -> None:
        """Confirm, then archive the live map and clear it."""
        map_name = self._topo_vm.map_name
        with ui.dialog() as dlg, ui.card():
            ui.label('Archive and clear map').classes('font-semibold')
            ui.label(
                f'Copies "{map_name}" to "{map_name}_N" then wipes all '
                f'nodes from the live map. Cannot be undone from the UI.'
            ).classes('text-xs').style('color:#57606a;max-width:340px')
            with ui.row().classes('w-full justify-end gap-2 mt-3'):
                ui.button('Cancel', on_click=lambda: dlg.submit('cancel')).props('flat no-caps')
                ui.button('Archive & Clear', color='negative',
                          on_click=lambda: dlg.submit('ok')).props('no-caps')
        if await dlg != 'ok':
            return
        self._show(self._topo_vm.archive_and_clear_map())
