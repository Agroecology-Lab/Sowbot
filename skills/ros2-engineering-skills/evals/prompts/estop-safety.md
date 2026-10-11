# E-Stop Isolation Challenge

## Scenario

You are hardening an autonomous forklift running ROS 2 (Jazzy). The vehicle already has
a physically-wired e-stop chain (buttons → safety relay → motor driver STO). The ROS 2
side has these nodes:

- `/safety_supervisor` — operator station node that should control the software stop
- `/nav_stack` — Nav2, publishes velocity commands at 20 Hz
- `/teleop` — joystick node for manual override
- `/base_controller` — drives the motors from `/cmd_vel`

A security audit and an incident review found:

1. The current software e-stop is a `std_msgs/Bool` on `/e_stop` where `true` means
   stop — when the supervisor node crashed during a test, the forklift kept driving.
2. During the stop test, Nav2 kept publishing `/cmd_vel` at 20 Hz and the zero-velocity
   command from the e-stop handler lost the race.
3. Any laptop on the warehouse Wi-Fi can join the DDS domain and publish `/e_stop` or
   `/cmd_vel` directly — a spoofed message could clear a stop or bypass it.
4. After a stop, the system un-stopped itself as soon as the trigger cleared, and the
   forklift lurched with the pre-stop command.

## Question

Redesign the software stop path: the e-stop topic pattern and QoS, command arbitration,
SROS2 isolation of the safety topics, and the reset behavior. Explain how each finding
is addressed and how the software layer relates to the hardware e-stop chain.
