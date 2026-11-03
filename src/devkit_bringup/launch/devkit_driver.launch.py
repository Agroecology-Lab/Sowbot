from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    """Build the driver launch description with port, simulation and cmd_vel options.

    handle_cmd_vel defaults to true; set it to false when another node drives
    the wheels from cmd_vel. simulation also sets FELDFREUND_SIMULATION.
    """
    port_arg = DeclareLaunchArgument(
        'port',
        default_value='virtual',
        description='Serial port for the MCU (e.g. /dev/ttyUSB0 or virtual)',
    )

    simulation_arg = DeclareLaunchArgument(
        'simulation',
        default_value='false',
        description='Run in simulation mode (true/false)',
    )

    handle_cmd_vel_arg = DeclareLaunchArgument(
        'handle_cmd_vel',
        default_value='true',
        description='true = drive the wheels from cmd_vel; false when another node (the MAVLink bridge) owns it',
    )

    simulation = LaunchConfiguration('simulation')

    set_simulation_env = SetEnvironmentVariable(
        'FELDFREUND_SIMULATION',
        simulation,
    )

    devkit_driver_node = Node(
        package='devkit_driver',
        executable='devkit_driver_node',
        name='devkit_driver_node',
        output='screen',
        parameters=[{
            'port': LaunchConfiguration('port'),
            'handle_cmd_vel': LaunchConfiguration('handle_cmd_vel'),
            'use_sim_time': simulation,
        }],
    )

    return LaunchDescription([
        port_arg,
        handle_cmd_vel_arg,
        simulation_arg,
        set_simulation_env,
        devkit_driver_node,
    ])
