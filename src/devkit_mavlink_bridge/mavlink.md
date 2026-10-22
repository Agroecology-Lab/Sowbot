# LEVIA H7 wiring and architecture

## Wiring

LEVIA H7 all-in-one. All connectors are 1.0 mm SH.

```
U24 (USART2, SERIAL3)              F9P #1 (base)
  1 TX  PD5  ------------------->  RX
  2 RX  PD6  <-------------------  TX
  3 GND  ------------------------  GND
  4 +5V_SYS ---------------------> VCC

U20 (UART4, SERIAL5)               F9P #2 (rover)
  1 +9V   DO NOT CONNECT
  3 TX  PB9  ------------------->  RX
  4 RX  PB8  <-------------------  TX
  5 GND  ------------------------  GND
  VCC <-- 5V tapped from another connector

U8 (USART6, SERIAL2)               Avaota (MAVLink)
  1 TX  PC6  ------------------->  RX
  2 RX  PC7  <-------------------  TX
  3 GND  ------------------------  GND
  4 +5V_SYS  leave unconnected, power the Avaota separately

U19 (SPI)                          Optical flow sensor
  1 +5V  ------------------------> VCC
  6 CS1  ------------------------  CS
  (remaining pins: SCK/MISO/MOSI/GND per the connector pinout)
```

The hwdef already defines the pixartflow device, so the sensor goes straight to the FC. No hwdef edit, no SBC involvement.

## Architecture

```
+------------------ Avaota SBC -----------------------------+
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
     MAVLink (UART: U8 / USART6 / SERIAL2) |   |
     in : pose/vel, GPS status, HEARTBEAT  |   |
     out: SET_POSITION_TARGET (drive),     |   |
          GPS_RTCM_DATA, HEARTBEAT         |   |
                                           |   v
+---------------- LEVIA-H7 (ArduPilot Rover) -------------------+
|                                                               |
|  F9P #1 (GPS1, base, U24)  --RTCM3, routed by FC-->           |
|                            F9P #2 (GPS2, rover, U20)          |
|        |  position, RTK                 |  moving-baseline    |
|        |                                |  heading (yaw)      |
|        +-------------+------------------+                     |
|                      |                                        |
|  wheel encoders -----+                                        |
|  optical flow -------+--->  EKF3  (sole estimator)            |
|   (SPI, U19, pixartflow     |  fuses GNSS + yaw + encoders    |
|    already in hwdef)        |  + flow + IMUs, innovation      |
|  IMU x2 (BMI270) -----------+  gating                         |
|                             |                                 |
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

## Fail-safe

All enforced on the LEVIA with no SBC involved. Flow, both GNSS receivers and the IMUs are wired to the FC, so EKF3 has every input without the SBC.

```
SBC or bridge dead        --> GUID_TIMEOUT stops, GCS failsafe holds
EKF unhealthy / GNSS lost --> EKF variance failsafe
stale cmd_vel             --> bridge sends zero (built)
fence breach              --> HOLD (not RTL)
```

## Bridge topics

Inbound, from the LEVIA-H7. No TF is broadcast.

| Topic | Type | Source message | Notes |
|---|---|---|---|
| `fc/odom` | `nav_msgs/Odometry` | LOCAL_POSITION_NED + ATTITUDE_QUATERNION | ENU, body FLU, from the EKF origin. Covariances unknown (-1 in element 0). Withheld unless EKF3 flags a valid pose and both inputs are fresh |
| `fc/ekf_fix` | `sensor_msgs/NavSatFix` | GLOBAL_POSITION_INT | EKF3 geodetic position. No fix unless EKF3 flags an absolute position. Altitude NaN (message is MSL) |
| `fc/gps1/fix`, `fc/gps2/fix` | `sensor_msgs/NavSatFix` | GPS_RAW_INT, GPS2_RAW | Receiver output, not EKF3. GPS1 = F9P #1 (base), GPS2 = F9P #2 (rover). RTK float is SBAS_FIX, RTK fixed is GBAS_FIX |
| `fc/diagnostics` | `diagnostic_msgs/DiagnosticArray` | HEARTBEAT, EKF_STATUS_REPORT, GPS_RAW_INT, GPS2_RAW | Link, EKF3 flags and variance ratios, GPS fix type, DOP, accuracy, heading. STALE when data stops |

The bridge asks the LEVIA-H7 for these messages with MAV_CMD_SET_MESSAGE_INTERVAL when its heartbeat appears and every 10 s after, so SRn_* parameters need not be set.

## To check

- Serial protocols are not default. GPS2 defaults to SERIAL4, so set SERIAL3_PROTOCOL and SERIAL5_PROTOCOL to GPS (5), SERIAL2_PROTOCOL to MAVLink2, GPS_TYPE=17 (moving-base base) and GPS_TYPE2=18 (rover).
- Set the flow params (FLOW_TYPE for PixArt) and confirm the U19 SPI pins against the hwdef before powering the sensor.
- Wheel encoders are in the architecture diagram but have no connector in the wiring section. Add one or drop them.
- The U19 pinout is incomplete. Only 5V (pin 1) and CS1 (pin 6) are specified.
