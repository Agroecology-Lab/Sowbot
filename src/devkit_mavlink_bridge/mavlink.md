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

## Real robot bring-up

Use `./manage.py`, not a hand-written `docker run`. It regenerates part of `.env`, checks the dashboard password, picks the DDS configuration and mounts the live source. A raw `docker run --env-file .env` uses whatever the last `manage.py` run wrote.

1. Build: `./manage.py full-build` (first time), or `./manage.py build`. The image needs `ros-jazzy-ntrip-client` for NTRIP.
<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:8c8bd37a070122c2ff697cc6544829bfda3c84af67da90ce70d8eb2a3bc9cfff -->

2. Create `.env` from `.env.example`. Set `MAVLINK_ENDPOINT` to a stable path such as `/dev/serial/by-id/...` (not `ttyACMn`, which can change), `MAVLINK_BAUD` if not 115200, `SOWBOT_UI_PASSWORD`, and `MAP_ORIGIN_LAT`, `MAP_ORIGIN_LON` and `MAP_YAW_DEG` from a survey. Set the caster host, port and mountpoint in `src/devkit_bringup/config/ntrip.yaml`.
   Copy `src/devkit_bringup/config/ntrip_secrets.yaml.template` to `src/devkit_bringup/config/ntrip_secrets.yaml` and fill in its username and password.
   This file follows the existing `config/secrets.yaml` pattern: it is gitignored and excluded from Docker builds.
   `manage.py` mounts `src/devkit_bringup` at runtime, and the installed launch uses that mount through the image’s symlink install.
   `devkit.launch.py` loads `ntrip.yaml` followed by `ntrip_secrets.yaml`, so credentials come from the mounted file.
   Keep credentials out of tracked `ntrip.yaml`; set `NTRIP_ENABLED=true` only after both files are configured.

<!-- /coderabbit-ai-generated:v1 -->
3. Bench, wheels off the ground. Power the LEVIA-H7 and its receivers. Do not arm yet.
4. Run `./manage.py`. It runs `sudo python3 tools/fixusb.py`, which asks for the safety acknowledgement the first time and so cannot be started unattended. It then starts the stack.
5. Check the log for `flight controller armed/disarmed, custom_mode`, STATUSTEXT lines from the flight controller, and `fc/diagnostics` (link, EKF3 flags, GPS fix type). Check `fc/gps1/fix` and `fc/gps2/fix`. With NTRIP on, `fc/gps1/fix` should reach RTK float or fixed.
6. Load `params/sowbot_rover.parm` and the fence (see Fence and parameters below), then arm and select GUIDED. Use a ground station on the board's USB port or the RC transmitter. The bridge's `fc/arm` and `fc/set_guided` services do the same but are off until `MAVLINK_ALLOW_ARM=true` (see Arm, disarm and GUIDED). Until the rover is armed in GUIDED, `cmd_vel` has no effect.
7. Follow the bench, hard standing and field steps in `docs/field-validation.md`. Do not enable `--restart` or any boot-time start until the stop sources in that document have been measured (O33, O36).

`.env` has two kinds of key. `tools/fixusb.py` rewrites the first on every launch and keeps the second as written.

| Rewritten every launch (do not edit) | Yours (edit once) |
|---|---|
| `SOWBOT_SAFETY_ACK`, `GPS_PORT_ROVER`, `GPS_PORT_ROVER1`, their `GPS_USB_PATH_*`, `GPS_TYPE_*` and `GPS_SERIAL_*`, `MCU_PORT`, `USB_CAM_*`, `AXIS_CAM_ENABLED`, `USER_ID`, `GROUP_ID`, `IS_JETSON`, `TMAP2_FILE` | `MAVLINK_ENDPOINT`, `MAVLINK_BAUD`, `MAVLINK_ALLOW_ARM`, `SOWBOT_UI_PASSWORD`, `MAP_ORIGIN_*`, `MAP_YAW_DEG`, `DDS_INTERFACE`, `TOOLS_BIND`, `FORCE_SIM`, `FIELD_DATUM_*` (sim only). `NTRIP_ENABLED` is rewritten with its current value, so setting it once sticks |

With `MAVLINK_ENDPOINT` set, `fixusb.py` writes `GPS_PORT_ROVER`, `GPS_PORT_ROVER1` and `MCU_PORT` as `virtual` whatever is plugged into the SBC, and logs the devices it ignored. Without an endpoint, a placeholder value counts as unset.

## Launch

Set `MAVLINK_ENDPOINT` (and `MAVLINK_BAUD` if not 115200) in `.env`. `manage.py` then runs in hardware mode with `GPS_PORT_ROVER` and `MCU_PORT` virtual, and `devkit.launch.py` starts the bridge with `publish_tf:=true`. A placeholder value (`TODO_SERIAL_DEVICE_HERE`) is refused by `manage.py` and `devkit.launch.py`.

With no endpoint set the bridge does not start and FusionCore runs. FusionCore publishes `/fusion/odom`, and a `topic_tools` relay in `devkit.launch.py` republishes it on `/fc/odom`, so Nav2 reads the same topic on both paths. `odom -> base_link` is owned by the identity static transform when `MCU_PORT` is virtual and sim is off, and by FusionCore otherwise.

- EKF3 is the localiser. Nav2 reads `/fc/odom` (`nav2_params.yaml`).
- With the bridge running, FusionCore is not launched. `_fusioncore_node()` in `devkit.launch.py` and `fusioncore.yaml` are only used when no endpoint is set. The sim stack (`sowbot_sim.launch.py`) still runs it.
- With the bridge running, nothing publishes `/fusion/odom`, so the UI loses fused odometry on that path. A relay the other way needs the bridge's unknown covariance handled first.
- The UI subscribes to `/gnss/fix`, `/fusion/odom`, `/odom` and `/odometry/global` and not to any `fc/*` topic. The bridge publishes none of them, so the UI has no position on this path and saving a topology row fails with no GPS fix. Relay `fc/gps1/fix` or `fc/ekf_fix` onto `/gnss/fix` (and settle its covariance) before using map authoring with the bridge.
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

Rover acts on these velocity targets only when armed in GUIDED. The bridge keeps forwarding `cmd_vel` while the rover is disarmed or in another mode. Watch the `flight controller armed/disarmed` log line.

## Arm, disarm and GUIDED

Three `std_srvs/Trigger` services, off the same MAVLink link. Not run against a flight controller yet; the tests use doubles.

| Service | Needs `allow_arm_from_ros` | Sends | Refused when |
|---|---|---|---|
| `fc/disarm` | no | MAV_CMD_COMPONENT_ARM_DISARM, param1 0 | no live flight controller heartbeat |
| `fc/set_guided` | yes | MAV_CMD_DO_SET_MODE, custom mode 15 (Rover GUIDED) | not permitted, or no live heartbeat |
| `fc/arm` | yes | MAV_CMD_COMPONENT_ARM_DISARM, param1 1, param2 0 | not permitted, no live heartbeat, mode is not GUIDED, or a fresh nonzero `cmd_vel` is present. Already armed: succeeds and sends nothing |

- `allow_arm_from_ros` defaults to false. Set `MAVLINK_ALLOW_ARM=true` in `.env` or pass the launch argument.
- `success: true` means the command was written to the link, not that the flight controller accepted it. The `COMMAND_ACK` result and the next heartbeat are logged ("flight controller accepted/refused arm/disarm"), and the reason for a refusal arrives as STATUSTEXT. A failed serial write returns `success: false`.
- Arming never uses the 21196 force value, so the flight controller's pre-arm checks always apply.
- `fc/disarm` is not gated, so any node on the ROS graph can call it. The flight controller decides whether to obey it while the rover is moving. That is not confirmed.
- With the fence enabled, Rover does not accept mode changes while in breach, so `fc/set_guided` will not recover a rover that has stopped at the fence.
- Any node on the ROS graph can call `fc/arm` once permitted. The DDS configuration and the dashboard password are the only access control.

## Fence and parameters

`params/sowbot_rover.parm` holds the serial, GPS, fence and failsafe parameters for Rover 4.6 or later, in the `NAME,value` form ground stations load. Proposed, not loaded on any flight controller. Load it over the board's USB port, reboot, and read the values back. The file leaves out the EKF3 yaw source, the moving-baseline antenna offsets (`GPS1_MB_*`) and `GUID_TIMEOUT`, which need measuring or checking against the firmware you flashed.

`tools/upload_fence.py` stores one inclusion polygon on the flight controller and reads it back:

```
tools/upload_fence.py --geojson farm-map.geojson --role field --field 1 --dry-run
tools/upload_fence.py --geojson farm-map.geojson --role field --field 1 --endpoint /dev/ttyACM0
```

- Use a link the bridge does not own, such as the board's USB port.
- The polygon must enclose everything the rover works in, with margin, and the rover must start inside it. A field boundary used unbuffered puts the outer rows on the limit.
- Without `FENCE_ENABLE`, `FENCE_TYPE` and `FENCE_ACTION` (in the parm file) the polygon does nothing.
- The tool handles one inclusion polygon. It refuses to read back a fence that holds anything else.
- Not run against a flight controller or SITL.

STATUSTEXT from the flight controller (PreArm failures, fence and failsafe text) is relayed to the ROS log at a matching severity. It is not yet published as a topic or fed to `/safety/level` (O19, O22 e).

RTCM reaches the flight controller only if something publishes `rtcm`. `ntrip_client` does, and `devkit.launch.py` starts it when `NTRIP_ENABLED=true` and either `MAVLINK_ENDPOINT` is set (real hardware only) or the `ublox` GPS path is selected on the SBC. `config/ntrip.yaml` has no NMEA GGA source from the flight controller, so a VRS mountpoint will not work. Use a fixed-position mountpoint, or add a GGA feed first.

## To check

- RTCM injection. GPS_INJECT_TO defaults to 127 (all receivers) and ArduPilot already skips the moving-baseline rover, so NTRIP data should reach F9P #1 only. Confirm on the bench that GPS1 reports RTK float or fixed in `fc/gps1/fix` once `rtcm` is flowing, and that the base still feeds the rover (GPS2 heading stays valid).
- Serial load on SERIAL5. State messages at 10 Hz plus RTCM at about 1 Hz is a few kB/s by my estimate, inside 115200 baud. Measure it on the vehicle rather than trust the estimate.
- Serial protocols are not default. Set SERIAL2_PROTOCOL and SERIAL3_PROTOCOL to GPS (5), SERIAL5_PROTOCOL to MAVLink2, GPS1_TYPE=17 (moving-base base) and GPS2_TYPE=18 (rover). Before Rover 4.6 these are GPS_TYPE and GPS_TYPE2. ArduPilot assigns GPS1 to the lowest-numbered port set to GPS, so the base must stay on SERIAL2 (U8) and the rover on SERIAL3 (U24). Swapping them makes the base GPS2 and breaks the moving baseline.
- USART6 (U8) may be NODMA in the hwdef, as on Matek H743. Check it. If so, the base F9P link has no DMA; fine at modest baud rates.
- Both F9Ps now draw from +5V_SYS, plus antenna bias current. Check the rail.
- Set FLOW_TYPE for the PixArt driver.
- The sensor board must take 5V power and work with 3.3V SPI logic. U19 supplies 5V and the MCU pins are 3.3V.
<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:c43c0ed2c002138d6ea92b3d78abf031f683bc6892eaab6ba3d0b61e250614d0 -->

- Yaw source. Confirm the EKF3 yaw source is the moving-baseline GPS heading (`GPS_AUTO_CONFIG`, `EK3_SRC1_YAW`) and measure the antenna offsets.
  For Rover 4.6, use `GPS1_MB_TYPE` and `GPS1_MB_OFS_X/Y/Z`; for older firmware (before 4.6), use `GPS_MB1_TYPE` and `GPS_MB1_OFS_X/Y/Z`.
  `manage.py` warns about the offsets using the older `GPS_MB1_OFS_*` names.

<!-- /coderabbit-ai-generated:v1 -->
- Fence and failsafes. `params/sowbot_rover.parm` and `tools/upload_fence.py` now set and upload them, but neither has run against a flight controller or SITL. The fence breach and failsafe behaviour above is a plan until exercised on the vehicle (O22). `GUID_TIMEOUT` is not set, and the "GUID_TIMEOUT ~1 s" in the diagram above is not confirmed for Rover: the Copter default is 3 s, which is what the bridge's watchdog assumes.
- Arm, disarm and GUIDED services. Check on the bench that the flight controller refuses `fc/arm` outside GUIDED and with a pre-arm failure, and what it does with `fc/disarm` while moving.
- Wheel encoders are in the architecture diagram but have no connector in the wiring section. Add one or drop them.
