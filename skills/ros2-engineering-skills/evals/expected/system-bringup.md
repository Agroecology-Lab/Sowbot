# Expected: System Bringup Design

## Key Elements

### udev Rules — Persistent Device Naming
- Rules file in `/etc/udev/rules.d/` (e.g. `99-robot.rules`)
- Match on `SUBSYSTEM=="tty"`, `ATTRS{idVendor}`, `ATTRS{idProduct}`, `ATTRS{serial}`
- `SYMLINK+="motor_controller"` and `SYMLINK+="lidar"` — drivers configured with the
  persistent symlink, never `/dev/ttyUSB0`
- `GROUP="dialout", MODE="0660"` for device permissions (no chmod in startup scripts)
- Reload and verify: `udevadm control --reload-rules`, `udevadm trigger`, `udevadm test`

### systemd Units — Boot Ordering
- Split units per failure domain (e.g. `ros2-base.service`, `ros2-nav.service`)
- `Wants=network-online.target` paired with `After=network-online.target`, and the
  matching `NetworkManager-wait-online.service` / `systemd-networkd-wait-online.service`
  enabled so DDS binds a real interface
- `After=time-sync.target` so sensor timestamps are not stamped before clock sync
- Nav layer ordered with `Requires=ros2-base.service` + `After=ros2-base.service`
  (Wants/Requires alone do not imply ordering)
- `Restart=on-failure` with `StartLimitBurst` escalation, not unbounded restart loops

### Watchdog — Detect the Silent Driver
- systemd `WatchdogSec` alone is insufficient — the process was alive while the loop
  was deadlocked
- Topic-level heartbeat published from inside the control loop (not a separate timer)
- Monitor with QoS DEADLINE events (`deadline_callback` on the subscription) or
  LIVELINESS `AUTOMATIC` with a lease duration — deadline missed triggers a safe stop
  (zero velocity command)

### Boot-Time Health Check
- Verify at the ROS graph level: wait for required nodes in `ros2 node list` and
  assert topic data flow with `ros2 topic echo --once` / `ros2 topic hz`
- Restart the CLI daemon (`ros2 daemon stop/start`) so the check sees the fresh graph
- Run as a oneshot systemd unit (`Type=oneshot`, `RemainAfterExit=yes`) after the base
  layer; report ready only when the check passes
