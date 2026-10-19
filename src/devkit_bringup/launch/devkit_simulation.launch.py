"""Launch file for the devkit project in simulation mode (driver + UI + Foxglove)."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource


def generate_launch_description():
    pkg_dir = get_package_share_directory('devkit_bringup')

    devkit_driver_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_dir, 'launch', 'devkit_driver.launch.py')
        ),
        launch_arguments={'simulation': 'true'}.items()
    )

    ui_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_dir, 'launch', 'ui.launch.py')
        )
    )

    # Loopback and view-only unless foxglove_address / foxglove_allow_control say otherwise
    foxglove_bridge = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_dir, 'launch', 'foxglove_bridge.launch.py')
        )
    )

    return LaunchDescription([
        devkit_driver_launch,
        ui_launch,
        foxglove_bridge,
    ])
