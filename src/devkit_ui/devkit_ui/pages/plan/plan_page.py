from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from devkit_ui.pages.delete_dialogs import confirm_delete_row
from devkit_ui.pages.obstacle_cards import attach_mission_obstacle_panel
from devkit_ui.pages.plan.boundary_map_card import BoundaryMapCard
from devkit_ui.pages.plan.mission_queue_card import MissionQueueCard
from devkit_ui.pages.plan.mission_store_card import MissionStoreCard
from devkit_ui.pages.plan.plan_controls_card import PlanControlsCard
from devkit_ui.view_models.mission_view_model import MissionViewModel
from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel
from devkit_ui.view_models.plan_view_model import PlanViewModel
from devkit_ui.view_models.topology_view_model import TopologyViewModel

if TYPE_CHECKING:
    from devkit_ui.view_models.map_authoring_view_model import MapAuthoringViewModel


class PlanPage(ui.column):
    """Everything for planning rows and running missions: boundary, planner, queue, saved missions and obstacles."""

    def __init__(self, plan_vm: PlanViewModel, mission_vm: MissionViewModel,
                 obstacle_vm: ObstacleViewModel, topo_vm: TopologyViewModel,
                 authoring: MapAuthoringViewModel, gps: tuple[float, float] | None) -> None:
        """Compose the Plan page from its cards, with state of its own for this browser session.

        Parameters:
            plan_vm (PlanViewModel): Plans, imports, saves and repairs rows, and opens the page state.
            mission_vm (MissionViewModel): The saved missions, this session's mission queue, and
                running or cancelling a mission.
            obstacle_vm (ObstacleViewModel): The obstacles drawn on the map and planned around.
            topo_vm (TopologyViewModel): Names the nodes of a row before it is deleted.
            authoring (MapAuthoringViewModel): Deletes a confirmed row from the map.
            gps (tuple[float, float] | None): The robot's (lat, lon) to centre the map on, or None.
        """
        super().__init__()
        self.classes('w-full gap-0')
        with self:
            state = plan_vm.open_page()
            with ui.row().classes('w-full gap-3 items-start mb-3'):
                boundary = BoundaryMapCard(state, gps)
                controls = PlanControlsCard(state, plan_vm, obstacle_vm, boundary.map)

            async def delete_row(row_id: int) -> None:
                """Ask for confirmation, then delete the row from the map."""
                await confirm_delete_row(topo_vm, row_id, authoring.delete_row)

            queue = mission_vm.new_queue()
            with MissionQueueCard(queue, plan_vm, mission_vm, delete_row) as queue_card:
                ui.separator().classes('my-3')
                MissionStoreCard(mission_vm, queue, queue_card.set_run_enabled)

            attach_mission_obstacle_panel(controls.draw_handle)
