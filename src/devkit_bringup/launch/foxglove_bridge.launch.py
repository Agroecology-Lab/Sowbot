"""Foxglove Bridge for visualisation, bound to loopback and without write access by default.

The bridge has no authentication. Its upstream defaults (address 0.0.0.0, clientPublish enabled,
every topic client-publishable) let any client on the network publish cmd_vel or estop/soft.
Both are therefore opt-in:

    foxglove_address:=0.0.0.0           accept connections from other hosts
    foxglove_allow_control:=true        let clients publish topics, set parameters and call services

Only enable either on a private network, with a physical e-stop within reach.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

VIEW_CAPABILITIES = ['connectionGraph', 'assets']
CONTROL_CAPABILITIES = ['clientPublish', 'parameters', 'parametersSubscribe', 'services',
                        'connectionGraph', 'assets']


def _bridge(capabilities: list[str], condition) -> Node:
    """Return the bridge node advertising the given capabilities when the condition holds."""
    return Node(
        package='foxglove_bridge',
        executable='foxglove_bridge',
        name='foxglove_bridge',
        condition=condition,
        parameters=[{
            'port': 8765,
            'address': ParameterValue(LaunchConfiguration('foxglove_address'), value_type=str),
            'tls': False,
            'capabilities': capabilities,
            'compression': 'jpeg',  # Use JPEG compression for images
            'jpeg_quality': 75,
            'max_qos_depth': 10,
        }],
    )


def generate_launch_description():
    """Declare the exposure arguments and start the bridge with matching capabilities."""
    return LaunchDescription([
        DeclareLaunchArgument('foxglove_address', default_value='127.0.0.1',
                              description='Address the unauthenticated Foxglove websocket binds to'),
        DeclareLaunchArgument('foxglove_allow_control', default_value='false',
                              description='Allow clients to publish topics, set parameters and call services'),
        _bridge(VIEW_CAPABILITIES, UnlessCondition(LaunchConfiguration('foxglove_allow_control'))),
        _bridge(CONTROL_CAPABILITIES, IfCondition(LaunchConfiguration('foxglove_allow_control'))),
    ])
