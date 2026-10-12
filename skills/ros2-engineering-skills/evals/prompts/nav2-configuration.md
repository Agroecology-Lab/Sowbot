# Nav2 Stack Configuration

## Scenario

Configure a Nav2 navigation stack for a differential-drive warehouse robot running ROS 2 Jazzy. The robot has a 2D LiDAR and wheel odometry.

## Requirements

- Configure Nav2 with AMCL for localization on a known map
- Use DWB local controller with appropriate parameters for indoor navigation
- Set up costmap with inflation layer and obstacle layer from LiDAR
- Configure the behavior tree navigator and the behavior server; spin and
  backup have been clearance-validated for this robot — explain the safety
  gating that justifies enabling them
- The robot footprint is 0.5m x 0.4m rectangular
- Hardware maximum speed: 1.2 m/s linear, 2.0 rad/s angular
- Operational speed limits for this site: 0.5 m/s linear, 1.0 rad/s angular
- Target distribution: Jazzy

## Question

Provide the complete Nav2 configuration (nav2_params.yaml) and a launch file that brings up the full navigation stack. Explain key parameter choices.
