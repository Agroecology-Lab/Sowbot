```
+------------------ Limbic SBC -----------------------------+
   |                                                            |
   |   Nav2  <---- pose, velocity (from FC EKF) ----+           |
   |     |                                          |           |
   |     | cmd_vel                    devkit_mavlink_bridge     |
   |     +----------------------------->  (needs inbound half)  |
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
