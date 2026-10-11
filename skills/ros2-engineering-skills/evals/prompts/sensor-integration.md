# Sensor Integration Challenge

## Scenario

You are integrating the perception hardware of an outdoor inspection robot running
ROS 2 (Jazzy): a 3D LiDAR (Ethernet, PTP-capable) and a global-shutter USB camera,
fused for obstacle detection. The current bringup shows these symptoms:

- `/detector` — fusion node that projects LiDAR points into the camera image
- `/lidar_driver` — publishes `/points` at 10 Hz
- `/camera_driver` — publishes `/camera/image_raw` at 30 Hz

Observed problems:

1. The fusion node throws `ExtrapolationException` several times per second when it
   looks up the transform for each cloud; a teammate "fixed" it by looking up the
   latest transform (`Time(0)`), and now obstacles smear sideways whenever the robot
   turns.
2. `ros2 topic delay /points` shows the LiDAR's timestamp offset growing by tens of
   milliseconds per hour.
3. The LiDAR-to-camera overlay looks aligned at 1 m but is clearly off at 10 m.
4. Image messages are published with `frame_id: camera_link`, and projected points
   appear rotated 90 degrees.

## Question

Fix the integration: driver configuration and QoS, sensor clock synchronization,
transform-synchronized processing in the fusion node, and the extrinsic calibration
workflow with its verification. Address each observed problem.
