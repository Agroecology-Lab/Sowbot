from nicegui import ui

from devkit_ui.pages.sim.gazebo_card import GazeboCard
from devkit_ui.pages.sim.map_archive_card import MapArchiveCard
from devkit_ui.pages.sim.plant_placement_card import PlantPlacementCard
from devkit_ui.pages.sim.row_follow_card import RowFollowCard
from devkit_ui.pages.sim.soil_texture_card import SoilTextureCard
from devkit_ui.view_models.sim_process_view_model import SimProcessViewModel
from devkit_ui.view_models.sim_view_model import SimViewModel
from devkit_ui.view_models.topology_view_model import TopologyViewModel


class SimPage(ui.column):
    """Everything for running the Gazebo simulation: world assets, launch controls, row follow and map archive."""

    def __init__(self, sim_vm: SimViewModel, topo_vm: TopologyViewModel,
                 process_vm: SimProcessViewModel) -> None:
        """Compose the Sim page from its cards.

        Parameters:
            sim_vm (SimViewModel): Access to the crop models and soil textures the world is built from.
            topo_vm (TopologyViewModel): The live map the world is generated from; saves copies of
                it and archives and clears it.
            process_vm (SimProcessViewModel): Starts, stops and reports on the sim and row-follow
                processes.
        """
        super().__init__()
        self.classes('w-full gap-3')
        with self:
            plant_placement = PlantPlacementCard(sim_vm).classes('w-full')
            GazeboCard(sim_vm, process_vm, plant_placement.settings).classes('w-full')
            RowFollowCard(process_vm).classes('w-full')
            SoilTextureCard(sim_vm).classes('w-full')
            MapArchiveCard(topo_vm).classes('w-full')
