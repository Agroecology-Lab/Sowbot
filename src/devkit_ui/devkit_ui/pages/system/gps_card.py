from nicegui import ui

from devkit_ui.constants import FIELD27_BOUNDS, FIELD27_CENTER
from devkit_ui.view_models.telemetry_view_model import TelemetryViewModel

_FIX_LABELS = {-1: 'NO FIX', 0: 'AUTONOMOUS', 1: 'SBAS', 2: 'DGNSS', 4: 'RTK FLOAT', 5: 'RTK FIXED'}


class GpsCard(ui.card):
    """Map marker and fix details for the latest GNSS message."""

    def __init__(self, telemetry_vm: TelemetryViewModel) -> None:
        """Build the map, centred on the field, and refresh it every 2 s.

        Parameters:
            telemetry_vm (TelemetryViewModel): Source of the latest GNSS message.
        """
        super().__init__()
        self._telemetry_vm = telemetry_vm
        with self:
            ui.label('GPS').classes('font-semibold mb-2')
            self._leaflet = ui.leaflet(center=FIELD27_CENTER, zoom=18).classes('w-full h-80')
            self._leaflet.run_map_method(
                'fitBounds', [list(FIELD27_BOUNDS[0]), list(FIELD27_BOUNDS[1])])
            self._marker = self._leaflet.marker(latlng=self._leaflet.center)
            self._status = ui.label('—').classes('text-xs font-mono mt-1').style('color:#57606a')
            ui.timer(2.0, self.refresh)

    def refresh(self) -> None:
        """Refresh the map marker and GPS details, keeping the display when no message exists."""
        gps = self._telemetry_vm.gps
        if gps is None:
            return
        lat, lon = gps.latitude, gps.longitude
        self._leaflet.set_center((lat, lon))
        self._marker.move(lat, lon)
        code = gps.status.status
        cov = gps.position_covariance[0]
        self._status.set_text(
            f'{_FIX_LABELS.get(code, str(code))}  '
            f'{lat:.6f}, {lon:.6f}  '
            f'alt={gps.altitude:.1f}m  '
            f'σ={cov**0.5:.2f}m')
        colour = '#1a7f37' if code == 5 else '#9a6700' if code >= 1 else '#cf222e'
        self._status.style(f'color:{colour}')
