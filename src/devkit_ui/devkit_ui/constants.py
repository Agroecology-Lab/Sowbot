NODE_NAME = 'nicegui'

# Row edges default to geometry-only traversal: it works with no crop present,
# which is the state when a map is first seeded. 'limbic_row_follow' is the
# vision-driven alternative.
ROW_ACTION = 'row_traversal'
VISION_ROW_ACTION = 'limbic_row_follow'
NAV_ACTION = 'navigate_to_pose'

# Field 27's actual GPS extent, derived from maps/recon_logs/recon.csv (the
# real Agri-Field-Dataset field-27 mesh, Zenodo 7805321 — France, ~372m x
# 252m footprint, downsampled to a ~6.5m grid — replacing an earlier
# placeholder India location that was in this file before). FIELD27_CENTER
# is the anchor the mesh was georeferenced against (the field's actual
# centroid). FIELD27_BOUNDS pads that extent by 15% on each side so
# leaflet's fitBounds() shows the whole field with a small margin, rather
# than butting the boundary against the map edge. This value MUST stay in
# lockstep with DEFAULT_FIELD_LAT/LON in topo_to_forest3d.py,
# FIELD_DATUM_LAT/LON's default in manage.py, and --anchor-lat/lon's
# default in maps/recon_logs/test_contour_planning.py. TelemetryDomainService
# publishes the sim GPS fallback at this datum, so a mismatch there is
# dangerous, not just cosmetic.
FIELD27_CENTER = (48.0046000, 3.6644000)
FIELD27_BOUNDS = ((48.0031957, 3.6612233), (48.0060043, 3.6675767))

# The saved topology map the simulated world is generated from (worldgen.sh reads it, and the
# Gazebo launch passes it to the sim as TMAP2_FILE).
SIM_MAP_FILE = '/workspace/maps/maize_map'
