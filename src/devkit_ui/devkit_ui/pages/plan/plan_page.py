from nicegui import ui

from devkit_ui.pages.obstacle_cards import attach_mission_obstacle_panel
from devkit_ui.pages.plan.boundary_map_card import BoundaryMapCard
from devkit_ui.pages.plan.mission_queue_card import MissionQueueCard
from devkit_ui.pages.plan.mission_store_card import MissionStoreCard
from devkit_ui.pages.plan.plan_controls_card import PlanControlsCard
from devkit_ui.view_models.mission_view_model import MissionViewModel
from devkit_ui.view_models.obstacle_view_model import ObstacleViewModel
from devkit_ui.view_models.plan_view_model import PlanViewModel


class PlanPage(ui.column):
    """Everything for planning rows and running missions: boundary, planner, queue, saved missions and obstacles."""

    def __init__(self, plan_vm: PlanViewModel, mission_vm: MissionViewModel,
                 obstacle_vm: ObstacleViewModel, gps: tuple[float, float] | None,
                 on_run, on_cancel, on_delete_row) -> None:
        """Compose the Plan page from its cards, with state of its own for this browser session.

        Parameters:
            plan_vm (PlanViewModel): Plans, imports, saves and repairs rows, and opens the page state.
            mission_vm (MissionViewModel): The saved missions, and this session's mission queue.
            obstacle_vm (ObstacleViewModel): The obstacles drawn on the map and planned around.
            gps (tuple[float, float] | None): The robot's (lat, lon) to centre the map on, or None.
            on_run (Callable): Called with the queued steps and a status label to run the mission.
            on_cancel (Callable): Called to cancel the running mission.
            on_delete_row (Callable): Called with a row id to delete it from the map.
        """
        super().__init__()
        self.classes('w-full gap-0')
        with self:
            state = plan_vm.open_page()
            with ui.row().classes('w-full gap-3 items-start mb-3'):
                boundary = BoundaryMapCard(state, gps)
                controls = PlanControlsCard(state, plan_vm, obstacle_vm, boundary.map)

            queue = mission_vm.new_queue()
            with MissionQueueCard(queue, plan_vm, on_run, on_cancel, on_delete_row) as queue_card:
                ui.separator().classes('my-3')
                MissionStoreCard(mission_vm, queue, queue_card.set_run_enabled)

            attach_mission_obstacle_panel(controls.draw_handle)
