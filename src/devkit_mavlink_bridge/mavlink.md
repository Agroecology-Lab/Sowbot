# LEVIA H7 wiring and architecture

[Tool](https://github.com/heeplr/rover-wizard)

## Wiring

LEVIA H7 all-in-one. All connectors are 1.0 mm SH.

```
U8 (USART6, SERIAL2)               F9P #1 (base)
  1 TX  PC6  ------------------->  RX
  2 RX  PC7  <-------------------  TX
  3 GND  ------------------------  GND
  4 +5V_SYS ---------------------> VCC

U24 (USART2, SERIAL3)              F9P #2 (rover)
  1 TX  PD5  ------------------->  RX
  2 RX  PD6  <-------------------  TX
  3 GND  ------------------------  GND
  4 +5V_SYS ---------------------> VCC

U20 (UART4, SERIAL5)               Avaota (MAVLink)
  1 +9V   DO NOT CONNECT
  3 TX  PB9  ------------------->  RX
  4 RX  PB8  <-------------------  TX
  5 GND  ------------------------  GND
  Power the Avaota separately

U19 (SPI3)                         Optical flow sensor
  1 +5V_SYS  --------------------> VCC
  2 GND  ------------------------  GND
  3 SCLK PB3  ------------------>  SCK
  4 MISO PB4  <------------------  MISO
  5 MOSI PB5  ------------------>  MOSI
  6 CS1  PD4  ------------------>  CS
```

The hwdef already defines the pixartflow device (SPI3, EXT_CS1 = PD4), which matches U19 pin 6. The sensor goes straight to the FC. No hwdef edit, no SBC involvement.

## Architecture

```
+------------------ Avaota SBC -----------------------------+
|                                                            |
|   Nav2  <---- pose, velocity (from FC EKF) ----+           |
|     |                                          |           |
|     | cmd_vel                    devkit_mavlink_bridge     |
|     +----------------------------->  (inbound + outbound)  |
|                                          ^   |             |
|   FusionCore: off when bridge runs       |   |             |
|     (runs when no endpoint is set)       |   |             |
+------------------------------------------|---|-------------+
                                           |   |
     MAVLink (UART: U20 / UART4 / SERIAL5) |   |
     in : pose/vel, GPS status, HEARTBEAT  |   |
     out: SET_POSITION_TARGET (drive),     |   |
          GPS_RTCM_DATA, HEARTBEAT         |   |
                                           |   v
+---------------- LEVIA-H7 (ArduPilot Rover) -------------------+
|                                                               |
|  F9P #1 (GPS1, base, U8)  --RTCM3, routed by FC-->            |
|                           F9P #2 (GPS2, rover, U24)           |
|        |  position, RTK                 |  moving-baseline    |
|        |                                |  heading (yaw)      |
|        +-------------+------------------+                     |
|                      |                                        |
|  wheel encoders -----+                                        |
|  optical flow -------+--->  EKF3  (sole estimator)            |
|   (SPI, U19, pixartflow     |  fuses GNSS + yaw + encoders    |
|    already in hwdef)        |  + flow + IMUs, innovation      |
|  IMU x2 (ICM-42688-P) ------+  gating                         |
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

## Launch

Set `MAVLINK_ENDPOINT` (and `MAVLINK_BAUD` if not 115200) in `.env`. `manage.py` then runs in hardware mode with `GPS_PORT_ROVER` and `MCU_PORT` virtual, and `devkit.launch.py` starts the bridge with `publish_tf:=true`. A placeholder value (`TODO_SERIAL_DEVICE_HERE`) is refused by `manage.py` and `devkit.launch.py`.

With no endpoint set the bridge does not start and FusionCore runs. FusionCore publishes `/fusion/odom`, and a `topic_tools` relay in `devkit.launch.py` republishes it on `/fc/odom`, so Nav2 reads the same topic on both paths. `odom -> base_link` is owned by the identity static transform when `MCU_PORT` is virtual and sim is off, and by FusionCore otherwise.

- EKF3 is the localiser. Nav2 reads `/fc/odom` (`nav2_params.yaml`).
- With the bridge running, FusionCore is not launched. `_fusioncore_node()` in `devkit.launch.py` and `fusioncore.yaml` are only used when no endpoint is set. The sim stack (`sowbot_sim.launch.py`) still runs it.
- With the bridge running, nothing publishes `/fusion/odom`, so the UI loses fused odometry on that path. A relay the other way needs the bridge's unknown covariance handled first.
- `manage.py` skips the `fusioncore.yaml` lever-arm check on this path. Set the antenna offsets on the flight controller.
- The bridge and the ESP32/Lizard `devkit_driver` both subscribe to `cmd_vel`. When the bridge runs, `devkit.launch.py` passes `handle_cmd_vel:=false` to the driver, so only the bridge acts on it.

## Map origin

`map -> odom` ties the topological map to the ground.
In real mode `devkit.launch.py` publishes it one of two ways:

- **`MAP_ORIGIN_LAT` and `MAP_ORIGIN_LON` set in `.env`** (degrees, the surveyed position of the map's (0, 0)): the `map_odom_anchor` node publishes it.
  `MAP_YAW_DEG` (default 0) is the direction of the map x axis, counter-clockwise from east.
  The node pairs `fc/ekf_fix` (or `gnss/fix` without the bridge) with the odom position on `fc/odom`.
  It publishes once, when the last 10 estimates agree within 0.5 m.
  Until then there is no `map -> odom`, so Nav2 waits.
  While it waits it logs a warning every 10 s that names the topic with nothing on it.
  Without the bridge, `gnss/fix` needs a GNSS receiver on `GPS_PORT_ROVER` or an external publisher.
  It does not re-anchor while running, so restart it if the EKF origin is reset.
  Setting only one of the two coordinates, or a latitude outside 90 degrees, stops the launch.
- **Not set:** an identity transform, so map waypoints are offset from the robot by wherever the localiser started.
  That is fine on the bench and wrong in a field.

The anchor assumes the odom frame is east-north-up.
`fc/odom` is (see the topics table).
On the FusionCore path this is untested.

## Bridge topics

Inbound, from the LEVIA-H7. `odom -> base_link` is broadcast only with `publish_tf` (launch default true), from the pose on `fc/odom` and under the same gating, so it stops when `fc/odom` stops.

| Topic | Type | Source message | Notes |
|---|---|---|---|
| `fc/odom` | `nav_msgs/Odometry` | LOCAL_POSITION_NED + ATTITUDE_QUATERNION | ENU, body FLU, from the EKF origin. Covariances unknown (-1 in element 0). Withheld unless EKF3 flags a valid pose and both inputs are fresh |
| `fc/ekf_fix` | `sensor_msgs/NavSatFix` | GLOBAL_POSITION_INT | EKF3 geodetic position. No fix unless EKF3 flags an absolute position. Altitude NaN (message is MSL) |
| `fc/gps1/fix`, `fc/gps2/fix` | `sensor_msgs/NavSatFix` | GPS_RAW_INT, GPS2_RAW | Receiver output, not EKF3. GPS1 = F9P #1 (base), GPS2 = F9P #2 (rover). RTK float is SBAS_FIX, RTK fixed is GBAS_FIX |
| `fc/diagnostics` | `diagnostic_msgs/DiagnosticArray` | HEARTBEAT, EKF_STATUS_REPORT, GPS_RAW_INT, GPS2_RAW | Link, EKF3 flags and variance ratios, GPS fix type, DOP, accuracy, heading. STALE when data stops |

The bridge asks the LEVIA-H7 for these messages with MAV_CMD_SET_MESSAGE_INTERVAL when its heartbeat appears and every 10 s after, so SRn_* parameters need not be set.

Outbound, to the LEVIA-H7.

| Topic | Type | MAVLink message | Notes |
|---|---|---|---|
| `cmd_vel` | `geometry_msgs/Twist` | SET_POSITION_TARGET_LOCAL_NED | Body frame, forward speed and yaw rate only. Clamped to `max_linear_x_mps` and `max_angular_z_radps`. Zero if the command is older than `cmd_vel_timeout_s` or the FC heartbeat is overdue |
| `rtcm` (`rtcm_topic`) | `rtcm_msgs/Message` | GPS_RTCM_DATA | One RTCM3 frame per message, as `ntrip_client` publishes it. Split into up to four 180-byte fragments; a frame over 720 bytes is dropped with a warning. Dropped while the FC heartbeat is overdue |
| none | none | HEARTBEAT | Sent once a second as a ground station, so the GCS failsafe sees a live link |

The bridge does not arm the rover or change its mode.

RTCM reaches the flight controller only if something publishes `rtcm`. `ntrip_client` does, but `devkit.launch.py` starts it only when `NTRIP_ENABLED=true` and its `ublox` GPS path is selected, which describes receivers on the SBC, not on the LEVIA-H7. Check that launch condition before relying on NTRIP.

## To check

- RTCM injection. GPS_INJECT_TO defaults to 127 (all receivers) and ArduPilot already skips the moving-baseline rover, so NTRIP data should reach F9P #1 only. Confirm on the bench that GPS1 reports RTK float or fixed in `fc/gps1/fix` once `rtcm` is flowing, and that the base still feeds the rover (GPS2 heading stays valid).
- Serial load on SERIAL5. State messages at 10 Hz plus RTCM at about 1 Hz is a few kB/s by my estimate, inside 115200 baud. Measure it on the vehicle rather than trust the estimate.
- Serial protocols are not default. Set SERIAL2_PROTOCOL and SERIAL3_PROTOCOL to GPS (5), SERIAL5_PROTOCOL to MAVLink2, GPS_TYPE=17 (moving-base base) and GPS_TYPE2=18 (rover). ArduPilot assigns GPS1 to the lowest-numbered port set to GPS, so the base must stay on SERIAL2 (U8) and the rover on SERIAL3 (U24). Swapping them makes the base GPS2 and breaks the moving baseline.
- USART6 (U8) may be NODMA in the hwdef, as on Matek H743. Check it. If so, the base F9P link has no DMA; fine at modest baud rates.
- Both F9Ps now draw from +5V_SYS, plus antenna bias current. Check the rail.
- Set FLOW_TYPE for the PixArt driver.
- The sensor board must take 5V power and work with 3.3V SPI logic. U19 supplies 5V and the MCU pins are 3.3V.
- Wheel encoders are in the architecture diagram but have no connector in the wiring section. Add one or drop them.
