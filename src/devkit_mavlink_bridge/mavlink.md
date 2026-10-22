```
+------------------ Limbic SBC -----------------------------+
   |                                                            |
   |   Nav2  <---- pose, velocity (from FC EKF) ----+           |
   |     |                                          |           |
   |     | cmd_vel                    devkit_mavlink_bridge     |
   |     +----------------------------->  (inbound + outbound)  |
   |                                          ^   |             |
   |   FusionCore (OPTIONAL, shadow only)     |   |             |
   |     logs and compares, never feeds FC    |   |             |
   +------------------------------------------|---|-------------+
                                              |   |
        MAVLink (USB-C or UART)               |   |
        in : pose/vel, GPS status, HEARTBEAT  |   |
        out: SET_POSITION_TARGET (drive),     |   |
             GPS_RTCM_DATA, HEARTBEAT         |   |
                                              |   v
   +---------------- LEVIA-H7 (ArduPilot Rover) -------------------+
   |                                                               |
   |  F9P #1 (GPS1, base)  --RTCM3-->  F9P #2 (GPS2, rover)        |
   |        |  position, RTK                 |  moving-baseline    |
   |        |                                |  heading (yaw)      |
   |        +-------------+------------------+                     |
   |                      |                                        |
   |  wheel encoders -----+                                        |
   |  optical flow -------+--->  EKF3  (sole estimator)            |
   |   (SPI3 hwdef edit,         |  fuses GNSS + yaw + encoders    |
   |    or OPTICAL_FLOW          |  + flow + IMUs, innovation      |
   |    from SBC)                |  gating                         |
   |  IMU x2 (BMI270) -----------+                                 |
   |                             |  ahrs.get_location()            |
   |                             v                                 |
   |                      native geofence                          |
   |                      (on EKF position)                        |
   |                             |                                 |
   |                      HOLD on breach                           |
   |                             |                                 |
   |                    GUIDED velocity target                     |
   |                    GUID_TIMEOUT ~1 s  --> stop                |
   |                             |                                 |
   |                        motor outputs --> tracks               |
   +---------------------------------------------------------------+
```

 Fail-safe, all enforced on the LEVIA with no SBC involved:
   SBC or bridge dead        --> GUID_TIMEOUT stops, GCS failsafe holds
   EKF unhealthy / GNSS lost --> EKF variance failsafe
   stale cmd_vel             --> bridge sends zero (built)
   fence breach              --> HOLD (not RTL)

Bridge topics (inbound, from the LEVIA-H7; no TF is broadcast):

| Topic | Type | Source message | Notes |
|---|---|---|---|
| `fc/odom` | `nav_msgs/Odometry` | LOCAL_POSITION_NED + ATTITUDE_QUATERNION | ENU, body FLU, from the EKF origin. Covariances unknown (-1 in element 0). Withheld unless EKF3 flags a valid pose and both inputs are fresh |
| `fc/ekf_fix` | `sensor_msgs/NavSatFix` | GLOBAL_POSITION_INT | EKF3 geodetic position. No fix unless EKF3 flags an absolute position. Altitude NaN (message is MSL) |
| `fc/gps1/fix`, `fc/gps2/fix` | `sensor_msgs/NavSatFix` | GPS_RAW_INT, GPS2_RAW | Receiver output, not EKF3. RTK float is SBAS_FIX, RTK fixed is GBAS_FIX |
| `fc/diagnostics` | `diagnostic_msgs/DiagnosticArray` | HEARTBEAT, EKF_STATUS_REPORT, GPS_RAW_INT, GPS2_RAW | Link, EKF3 flags and variance ratios, GPS fix type, DOP, accuracy, heading. STALE when data stops |

The bridge asks the LEVIA-H7 for these messages with MAV_CMD_SET_MESSAGE_INTERVAL when its
heartbeat appears and every 10 s after, so SRn_* parameters need not be set.
