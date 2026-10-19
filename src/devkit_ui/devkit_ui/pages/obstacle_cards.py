"""NiceGUI cards for marking, drawing and listing obstacles on the Run and Plan tabs."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel

_log = logging.getLogger(__name__)


def _remove_layer(layer) -> None:
    """Remove a leaflet layer from the map; a failure (for example a client that already left) is logged at debug."""
    try:
        layer.run_method('remove')
    except Exception:
        _log.debug('could not remove map layer', exc_info=True)


# ── UI: Nav-tab attachment ────────────────────────────────────────────────────

def attach_nav_card(vm: ObstacleViewModel) -> None:
    """
    Render the “Mark Obstacle” card and keep its status display synchronized with the view model.

    Parameters:
        vm (ObstacleViewModel): State and intents for marking and undoing obstacles.
    """
    with ui.card().style('padding:12px 14px;flex-shrink:0;min-width:200px'):
        with ui.row().classes('items-baseline gap-2 mb-2'):
            ui.label('Mark Obstacle').classes('font-semibold')
            ui.label('circle at current GPS').classes('text-xs').style(
                'color:#8c959f')
        with ui.row().classes('items-center gap-2 w-full'):
            obs_name = ui.input(
                placeholder='blank → OBS_N', label='Name',
            ).classes('flex-1')
        with ui.row().classes('items-center gap-2 w-full mt-1'):
            ui.number(
                label='Radius', value=vm.default_radius,
                min=0.1, max=5.0, step=0.1, precision=2, suffix='m',
            ).classes('w-24').bind_value(vm, 'default_radius')

            def _mark_here() -> None:
                """
                Mark a circular obstacle at the latest GPS position.

                If no GPS message is available, the view model reports an error in the status.
                On success, clears the obstacle name input and displays a temporary dialog
                with an option to undo the addition.
                """
                added = vm.mark_here(obs_name.value or '')
                if added:
                    obs_name.set_value('')
                    # Using Dialog because NiceGUI notification dismissal cannot
                    # reliably report whether Undo or its timeout closed the toast.
                    with ui.dialog() as undo_dialog, ui.element('div').classes(
                            'obstacle-undo-notification'):
                        with ui.row().classes('items-center gap-3'):
                            ui.label(f'Marked {added}')
                            ui.button(
                                'Undo',
                                on_click=lambda: (
                                    vm.delete(added), _close_undo_dialog()),
                            ).props('flat dense').classes('text-white')
                    undo_dialog.props('position=bottom seamless')
                    undo_dialog.open()

                    def _close_undo_dialog() -> None:
                        """Close the obstacle undo dialog if it is still available."""
                        if not undo_dialog.is_deleted:
                            undo_dialog.close()

                    ui.timer(5.0, _close_undo_dialog, once=True)

            ui.button('Mark here', on_click=_mark_here).classes(
                'ml-auto').props('color=warning no-caps dense')

        status_lbl = ui.label('').classes('text-xs font-mono mt-1')

    _prev = ['']

    def _refresh() -> None:
        """Update the Nav card's status text and color when the message changes."""
        cur = vm.status
        if cur == _prev[0]:
            return
        _prev[0] = cur
        status_lbl.set_text(cur)
        status_lbl.style(
            'color:#cf222e' if cur.startswith('ERROR') else
            'color:#1a7f37' if cur else 'color:#57606a')

    ui.timer(0.3, _refresh)


# ── UI: Mission-tab attachment ────────────────────────────────────────────────
#
# Split in two — the controls go in the sidebar (narrow), the list panel
# goes below the map (wide). The MissionDrawHandle returned by the first
# call is passed to the second so they share in-progress drawing state and
# the click handler.

class MissionDrawHandle:
    """State + accessors shared between the sidebar controls and the list
    panel. Created by attach_mission_sidebar_controls; consumed by
    attach_mission_obstacle_panel."""

    def __init__(self, vm: ObstacleViewModel, mission_map,
                 obstacle_pad_widget) -> None:
        """Store shared widgets and initialize drawing state and obstacle layers."""
        self.vm           = vm
        self.mission_map  = mission_map
        self.obstacle_pad = obstacle_pad_widget
        # In-progress polygon state
        self.corners:  list[tuple[float, float]] = []
        self.layer:    list = [None]
        self.markers:  list = []
        # Rendered saved-obstacle layers keyed by name → leaflet layer
        self.rendered: dict = {}
        # Current draw mode + shape — set by toggle handlers
        self.draw_mode = ['boundary']
        self.obs_shape = ['circle']

    def clear_in_progress(self) -> None:
        """Tear down any in-progress polygon. Called by the outer Clear
        button so user intent ('clear everything') actually clears."""
        self.corners.clear()
        if self.layer[0] is not None:
            _remove_layer(self.layer[0])
            self.layer[0] = None
        for m in self.markers:
            _remove_layer(m)
        self.markers.clear()


def attach_mission_sidebar_controls(
        vm: ObstacleViewModel, mission_map,
        boundary_click: Callable[[float, float], None]) -> MissionDrawHandle:
    """Renders the Field/Obstacle draw-mode toggle, shape sub-toggle,
    radius input, obstacle padding input, and Finish-polygon button in
    the current ui context (the Mission sidebar column).

    Also wires the mission_map's 'map-click' handler to dispatch between
    boundary-drawing (callback) and obstacle-drawing (view model).

    Returns a handle the caller passes to attach_mission_obstacle_panel.
    """
    ui.html('<div class="sec-label mt-3">Draw mode</div>')
    mode_toggle = ui.toggle(
        {'boundary': 'Field', 'obstacle': 'Obstacle'},
        value='boundary',
    ).props('dense').classes('w-full')

    with ui.column().classes('w-full gap-2 mt-2') as obs_controls:
        ui.html('<div class="sec-label">Obstacle shape</div>')
        shape_toggle = ui.toggle(
            {'circle': 'Circle', 'polygon': 'Polygon'},
            value='circle',
        ).props('dense').classes('w-full')

        ui.html('<div class="sec-label">Radius (circle)</div>')
        ui.number(value=vm.default_radius,
                  min=0.1, max=5.0, step=0.1, precision=2,
                  suffix='m').classes('w-full').bind_value(
                      vm, 'default_radius')

        finish_btn = ui.button('Finish polygon').props(
            'outline no-caps dense').classes('w-full')

    obs_controls.set_visibility(False)

    ui.html('<div class="sec-label mt-3">Obstacle padding</div>')
    obstacle_pad = ui.number(
        value=0.5, min=0.0, step=0.1, precision=2, suffix='m',
    ).classes('w-full')
    ui.label('robot clearance during planning').classes(
        'text-xs').style('color:#8c959f')

    handle = MissionDrawHandle(vm, mission_map, obstacle_pad)

    def _on_mode(e):
        """Select the draw mode and show obstacle controls only in obstacle mode."""
        handle.draw_mode[0] = e.args
        obs_controls.set_visibility(e.args == 'obstacle')

    def _on_shape(e):
        """Select the obstacle shape used for subsequent map clicks."""
        handle.obs_shape[0] = e.args

    mode_toggle.on('update:model-value',  _on_mode)
    shape_toggle.on('update:model-value', _on_shape)

    def _redraw_in_progress() -> None:
        """Replace the polygon preview, drawing it when at least two corners exist."""
        if handle.layer[0] is not None:
            _remove_layer(handle.layer[0])
            handle.layer[0] = None
        if len(handle.corners) >= 2:
            latlngs = [[la, lo] for la, lo in handle.corners]
            handle.layer[0] = mission_map.generic_layer(
                name='polygon',
                args=[latlngs,
                      {'color': '#cf222e', 'fillOpacity': 0.15,
                       'weight': 2, 'dashArray': '4 3'}],
            )

    def _finish_polygon() -> None:
        """Submit the polygon corners and clear the drawing only if accepted."""
        added = vm.add_polygon(list(handle.corners))
        # On failure (dup name etc.) keep in-progress state for retry.
        # Only tear down on success.
        if added is not None:
            handle.clear_in_progress()

    finish_btn.on_click(_finish_polygon)

    def _on_map_click(e) -> None:
        """Route a map click to the boundary, a circle, or an in-progress polygon."""
        lat = e.args['latlng']['lat']
        lon = e.args['latlng']['lng']
        if handle.draw_mode[0] == 'boundary':
            boundary_click(lat, lon)
            return
        if handle.obs_shape[0] == 'circle':
            vm.add_circle(lat, lon)
            return
        # polygon: accumulate corners
        handle.corners.append((lat, lon))
        handle.markers.append(mission_map.marker(latlng=(lat, lon)))
        _redraw_in_progress()

    mission_map.on('map-click', _on_map_click)
    return handle


def attach_mission_obstacle_panel(handle: MissionDrawHandle) -> None:
    """Renders the obstacle list + Leaflet rendering panel in the current
    ui context. Call from below the map (wide layout)."""
    vm          = handle.vm
    mission_map = handle.mission_map

    with ui.card().classes('w-full mt-3'):
        with ui.row().classes('items-baseline gap-2 mb-2'):
            ui.label('Obstacles').classes('font-semibold')
            count_lbl = ui.label('').classes('text-xs').style('color:#8c959f')
        list_col   = ui.column().style('gap:2px;width:100%')
        status_lbl = ui.label('').classes('text-xs font-mono mt-2').style(
            'color:#57606a;word-break:break-word')

    _prev = {'version': -1, 'status': None}

    def _refresh() -> None:
        """Refresh status on changes and rebuild the list and map layers on a new version."""
        v      = vm.version
        status = vm.status
        if v == _prev['version'] and status == _prev['status']:
            return
        version_changed  = v != _prev['version']
        _prev['version'] = v
        _prev['status']  = status

        status_lbl.set_text(status)
        status_lbl.style(
            'color:#cf222e' if status.startswith('ERROR') else
            'color:#1a7f37' if status else 'color:#57606a')

        if not version_changed:
            return

        obstacles = vm.obstacles
        count_lbl.set_text(f'{len(obstacles)} saved')

        list_col.clear()
        if not obstacles:
            with list_col:
                ui.label('No obstacles yet').classes('text-xs').style(
                    'color:#8c959f')
        else:
            with list_col:
                for o in obstacles:
                    nm = o.get('name', '?')
                    if o.get('type') == 'circle':
                        c    = o.get('center', {})
                        desc = (f'circle r={o.get("radius_m")}m  '
                                f'({c.get("lat"):.5f}, {c.get("lon"):.5f})')
                    else:
                        desc = f'polygon ({len(o.get("points", []))} pts)'
                    with ui.row().classes('items-center gap-2 w-full'):
                        ui.label(nm).classes('text-sm font-mono').style(
                            'min-width:90px')
                        ui.label(desc).classes(
                            'text-xs font-mono flex-1').style('color:#8c959f')
                        ui.button(
                            '✕', on_click=lambda _, n=nm: vm.delete(n),
                        ).props('flat dense').classes('text-xs').style(
                            'color:#cf222e')

        # Map layers: clear-all then redraw-all. Cheap at obstacle counts < 1000.
        for lyr in handle.rendered.values():
            _remove_layer(lyr)
        handle.rendered.clear()
        for o in obstacles:
            nm = o.get('name', '?')
            if o.get('type') == 'circle':
                c = o.get('center', {})
                handle.rendered[nm] = mission_map.generic_layer(
                    name='circle',
                    args=[[c.get('lat'), c.get('lon')],
                          {'radius':      float(o.get('radius_m', 0.5)),
                           'color':       '#cf222e',
                           'fillColor':   '#cf222e',
                           'fillOpacity': 0.35,
                           'weight':      2}],
                )
            else:
                latlngs = [[p['lat'], p['lon']]
                           for p in o.get('points', [])]
                handle.rendered[nm] = mission_map.generic_layer(
                    name='polygon',
                    args=[latlngs,
                          {'color':       '#cf222e',
                           'fillColor':   '#cf222e',
                           'fillOpacity': 0.35,
                           'weight':      2}],
                )

    ui.timer(0.5, _refresh)
