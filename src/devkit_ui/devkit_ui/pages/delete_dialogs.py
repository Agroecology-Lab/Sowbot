"""Confirmation dialogs for deleting topology nodes and rows."""
from collections.abc import Callable

from nicegui import ui

from devkit_ui.view_models.topology_view_model import TopologyViewModel


async def confirm_delete_node(topo_vm: TopologyViewModel, name: str | None,
                              on_confirm: Callable[[str], None]) -> None:
    """Ask for confirmation before deleting a topology node, then call on_confirm(name).

    With a loaded map, ignore an empty name or a node absent from that map.
    Cancelling the dialog leaves the node unchanged.
    """
    doc = topo_vm.topo_doc
    if not name or doc is None or not doc.has_node(name):
        return
    row_id = doc.get_node(name).meta.get('row_id')
    with ui.dialog() as dialog, ui.card():
        ui.label(f'Delete topo node "{name}"?').classes('font-semibold')
        if row_id is not None:
            ui.label(
                f'This is part of row {row_id}. To delete the whole row '
                f'(entry + exit), use the ✕ on the Plan tab instead.'
            ).classes('text-xs').style('color:#9a6700;max-width:340px')
        ui.label('This persists immediately and cannot be undone.').classes(
            'text-xs').style('color:#8c959f')
        _buttons(dialog)
    if await dialog == 'ok':
        on_confirm(name)


async def confirm_delete_row(topo_vm: TopologyViewModel, row_id: int,
                             on_confirm: Callable[[int], None]) -> None:
    """Ask for confirmation before deleting every node of a row, then call on_confirm(row_id).

    Do nothing when the row has no nodes in the loaded map.
    """
    doc = topo_vm.topo_doc
    targets = sorted(node.name for node in doc.nodes
                     if node.meta.get('row_id') == row_id) if doc else []
    if not targets:
        return
    with ui.dialog() as dialog, ui.card():
        ui.label(f'Delete row {row_id}?').classes('font-semibold')
        ui.label(f'{len(targets)} nodes will be removed:').classes('text-xs').style(
            'color:#57606a')
        ui.label(', '.join(targets)).classes('text-xs font-mono').style(
            'color:#8c959f;max-width:340px;word-break:break-all')
        _buttons(dialog)
    if await dialog == 'ok':
        on_confirm(row_id)


def _buttons(dialog: ui.dialog) -> None:
    """Add the Cancel and Delete buttons, which submit 'cancel' and 'ok'."""
    with ui.row().classes('w-full justify-end gap-2 mt-2'):
        ui.button('Cancel', on_click=lambda: dialog.submit('cancel')).props('flat no-caps')
        ui.button('Delete', color='negative',
                  on_click=lambda: dialog.submit('ok')).props('no-caps')
