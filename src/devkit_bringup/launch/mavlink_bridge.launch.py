"""Start the cmd_vel -> ArduPilot MAVLink bridge.

Opt-in: nothing else launches it, and the legacy ESP32/Lizard driver path also consumes
cmd_vel. Run one controller path at a time. The connection comes from the environment:

    MAVLINK_ENDPOINT   serial device or connection string (required, no default)
    MAVLINK_BAUD       defaults to 115200; must match SERIALn_BAUD on the flight controller

Limits below are starting values: measure the real stopping distance and turn rate.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    arguments = {
        'cmd_vel_timeout_s': ('0.5', float),
        'max_linear_x_mps': ('1.6', float),
        'max_angular_z_radps': ('1.0', float),
        'fc_heartbeat_timeout_s': ('3.0', float),
        'require_fc_heartbeat': ('true', bool),
    }
    declarations = [DeclareLaunchArgument(name, default_value=default)
                    for name, (default, _) in arguments.items()]
    parameters = {name: ParameterValue(LaunchConfiguration(name), value_type=kind)
                  for name, (_, kind) in arguments.items()}
    return LaunchDescription([
        *declarations,
        Node(
            package='devkit_mavlink_bridge',
            executable='mavlink_bridge_node',
            name='devkit_mavlink_bridge_node',
            output='screen',
            parameters=[parameters],
        ),
    ])
