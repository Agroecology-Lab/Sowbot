# Expected: Sensor Integration Fixes

## Key Elements

### Driver Configuration and QoS
- Sensor topics published with sensor-data QoS (`qos_profile_sensor_data` /
  `SensorDataQoS`: BEST_EFFORT, shallow KEEP_LAST) — not RELIABLE
- Persistent device naming via udev symlinks, static IP on a dedicated NIC for the
  Ethernet LiDAR
- Camera `frame_id` must be the optical frame (`camera_optical_frame`, z forward,
  x right, y down), with the fixed body-to-optical rotation modeled in the URDF —
  this fixes the 90-degree rotation
- `camera_info_url` pointing at the intrinsic calibration YAML

### Clock Synchronization
- Discipline the LiDAR clock over PTP (`linuxptp`: `ptp4l` + `phc2sys`, hardware
  timestamping NIC) or fall back to host-time stamping — fixes the growing offset
- chrony for the OS clock, stepping only at boot (`makestep`)
- Verify with `ros2 topic delay` on each sensor topic and compare relative skew
  between camera and LiDAR stamps

### Transform-Synchronized Processing
- Replace the direct `lookup_transform` (and the `Time(0)` workaround) with
  `tf2_ros::MessageFilter` over a `message_filters::Subscriber` — messages are
  buffered until the transform at their `header.stamp` is available
- Queue size sized to tf latency times message rate; buffer needs a
  `CreateTimerROS` timer interface
- Looking up the latest transform with old data is what smears obstacles during
  turns — always look up at the message stamp

### Extrinsic Calibration and Verification
- Target-based LiDAR-camera extrinsic calibration (checkerboard poses seen by both
  sensors), starting from the CAD/URDF mounting values
- Write the calibrated transform back into the URDF joint origin (single source of
  truth)
- Verify by re-projection: project LiDAR points onto the image with
  `cv2.projectPoints` and check edge alignment at near AND far range — misalignment
  only at 10 m indicates a rotation error, so re-calibrate with far-field poses
