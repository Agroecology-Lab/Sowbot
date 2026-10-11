# System Bringup Challenge

## Scenario

You are deploying a ROS 2 (Jazzy) delivery robot that must boot headless into a fully
operational state — no operator, no SSH session. The compute is a Jetson Orin running
Ubuntu 24.04. The system has:

- `/base_driver` — motor controller over USB-serial (FTDI, currently `/dev/ttyUSB0`)
- `/lidar_driver` — 2D LiDAR over USB-serial (CP2102, currently `/dev/ttyUSB1`)
- `/nav_stack` — Nav2, must start only after the drivers are up

Observed problems in the field:

1. After some reboots the motor controller and LiDAR swap between `ttyUSB0`/`ttyUSB1`,
   and the base driver opens the LiDAR's port.
2. When the robot boots faster than Wi-Fi associates, DDS binds only the loopback
   interface and nodes never discover each other.
3. Once, the base driver process stayed alive but its control loop deadlocked — the
   robot kept driving with stale commands and nothing detected it.
4. Operations wants the robot to report "ready" only when nodes are actually publishing,
   not merely when systemd started the processes.

## Question

Design the bringup: udev rules for stable device naming, systemd units with correct
startup ordering, a watchdog that catches the silent-driver failure, and a boot-time
health check. Show the udev rules file, the systemd unit files, and the watchdog approach.
