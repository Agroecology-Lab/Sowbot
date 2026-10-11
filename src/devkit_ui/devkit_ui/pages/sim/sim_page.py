from collections.abc import Callable

from nicegui import ui

from devkit_ui.pages.sim.gazebo_card import GazeboCard
from devkit_ui.pages.sim.map_archive_card import MapArchiveCard
from devkit_ui.pages.sim.plant_placement_card import PlantPlacementCard
from devkit_ui.pages.sim.row_follow_card import RowFollowCard
from devkit_ui.pages.sim.soil_texture_card import SoilTextureCard
from devkit_ui.view_models.sim_view_model import SimViewModel


class SimPage(ui.column):
    """Everything for running the Gazebo simulation: world assets, launch controls, row follow and map archive."""

    def __init__(self,
                 sim_vm: SimViewModel,
                 map_name: Callable[[], str],
                 on_save_map: Callable[[str], str],
                 on_archive_map: Callable[[], str]) -> None:
        """Compose the Sim page from its cards.

        Parameters:
            sim_vm (SimViewModel): Access to the crop models and soil textures the world is built from.
            map_name (Callable[[], str]): Returns the live map's name.
            on_save_map (Callable[[str], str]): Saves a named map copy and returns a status message.
            on_archive_map (Callable[[], str]): Archives and clears the live map and returns a status message.
        """
        super().__init__()
        self.classes('w-full gap-3')
        with self:
            plant_placement = PlantPlacementCard(sim_vm).classes('w-full')
            GazeboCard(sim_vm, plant_placement.settings).classes('w-full')
            RowFollowCard().classes('w-full')
            SoilTextureCard(sim_vm).classes('w-full')
            MapArchiveCard(map_name, on_save_map, on_archive_map).classes('w-full')
