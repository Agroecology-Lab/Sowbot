from nicegui import ui

from devkit_ui.view_models.plan_view_model import RepairOffer


async def ask_repair_base(offer: RepairOffer) -> str | None:
    """Ask whether to repair row connectivity and which node to splice the chain into.

    Parameters:
        offer (RepairOffer): The row count to mention and the suggested base node.

    Returns:
        str | None: None if the user cancelled. Otherwise the base node name they entered,
        stripped; empty means wire the chain on its own.
    """
    with ui.dialog() as dialog, ui.card():
        ui.label('Repair row connectivity').classes('font-semibold')
        ui.label(
            f'Add navigate_to_pose edges between {offer.row_count} consecutive '
            f'row IN/OUT pairs. Optionally splice the chain into a base '
            f'node so the planner can reach it.'
        ).classes('text-xs').style('color:#57606a;max-width:340px')
        base_input = ui.input(
            label='Splice into',
            placeholder='leave blank for chain only',
            value=offer.default_base,
        ).classes('w-full mt-2')
        ui.label(
            'Without a base node the chain is wired internally but '
            'remains unreachable from the rest of the graph.'
        ).classes('text-xs').style('color:#9a6700;max-width:340px;margin-top:4px')
        with ui.row().classes('w-full justify-end gap-2 mt-2'):
            ui.button('Cancel', on_click=lambda: dialog.submit('cancel')).props('flat no-caps')
            ui.button('Repair', color='positive',
                      on_click=lambda: dialog.submit('ok')).props('no-caps')

    if await dialog != 'ok':
        return None
    return (base_input.value or '').strip()
