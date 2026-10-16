from nicegui import ui

from devkit_ui.constants import FIELD27_BOUNDS, FIELD27_CENTER
from devkit_ui.view_models.plan_view_model import PlanPageState

BOUNDARY_STYLE = {'color': '#1a7f37', 'fillOpacity': 0.15, 'weight': 2, 'dashArray': '6 4'}
SWATH_STYLE = {'color': '#0969da', 'weight': 2, 'opacity': 0.85}


class BoundaryMapCard(ui.card):
    """Satellite map for drawing the field boundary, showing the corners and the planned rows."""

    def __init__(self, state: PlanPageState, gps: tuple[float, float] | None) -> None:
        """Build the map and keep it in step with the page state.

        Parameters:
            state (PlanPageState): The boundary corners and the draft rows to draw. The map
                redraws whenever it changes. Clicks on the map are handled by the controls card.
            gps (tuple[float, float] | None): The robot's (lat, lon) to centre on, or None to
                fit the whole field extent.
        """
        super().__init__()
        self.classes('flex-1').style('padding:10px;min-width:0')
        self._state = state
        self._markers: list = []
        self._polygon = None
        self._swath_layers: list = []
        self._drawn_draft = None

        with self:
            ui.html('<div class="sec-label mb-2">Field boundary — click to draw</div>')
            self.map = ui.leaflet(center=gps or FIELD27_CENTER, zoom=18).classes('w-full h-96')
            if gps is None:
                # No live fix yet: fit the whole field extent rather than zooming in on its
                # centre point, so the boundary-drawing view actually shows the field.
                self.map.run_map_method(
                    'fitBounds', [list(FIELD27_BOUNDS[0]), list(FIELD27_BOUNDS[1])])
            self.map.tile_layer(
                url_template='https://server.arcgisonline.com/ArcGIS/rest/services/'
                             'World_Imagery/MapServer/tile/{z}/{y}/{x}',
                options={'attribution': 'Esri', 'maxZoom': 20},
            )
        state.subscribe(self.refresh)

    def refresh(self) -> None:
        """Redraw the corners and the polygon if the boundary changed, and the rows if the draft did."""
        corners = self._state.corners
        if len(corners) != len(self._markers):
            self._draw_boundary(corners)
        if self._state.draft is not self._drawn_draft:
            self._draw_swaths()

    def _draw_boundary(self, corners: list[tuple[float, float]]) -> None:
        """Bring the corner markers and the polygon outline in line with the current corners."""
        if len(corners) < len(self._markers):
            for marker in self._markers:
                self.map.remove_layer(marker)
            self._markers = []
        for corner in corners[len(self._markers):]:
            self._markers.append(self.map.marker(latlng=corner))
        if self._polygon is not None:
            self.map.remove_layer(self._polygon)
            self._polygon = None
        if len(corners) >= 2:
            self._polygon = self.map.generic_layer(
                name='polygon', args=[[list(c) for c in corners], BOUNDARY_STYLE])

    def _draw_swaths(self) -> None:
        """Replace the drawn rows with the draft's swaths."""
        for layer in self._swath_layers:
            self.map.remove_layer(layer)
        self._swath_layers = [
            self.map.generic_layer(name='polyline', args=[[list(p) for p in swath], SWATH_STYLE])
            for swath in self._state.swaths]
        self._drawn_draft = self._state.draft
