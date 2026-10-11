# Expected: E-Stop Isolation Design

## Key Elements

### Fail-Safe Heartbeat Topic
- Replace "send true to stop" with a motion-permit heartbeat — silence stops the robot
  (fail-safe, not fail-dangerous)
- QoS: RELIABLE, KEEP_LAST depth 1, DEADLINE (e.g. 500 ms), LIFESPAN so stale permits
  are never delivered
- Consumer uses the subscription `deadline_callback` QoS event to engage the stop when
  the heartbeat goes silent
- Publisher must offer a deadline ≤ requested (RxO) or the pair never matches
- Separate latched `TRANSIENT_LOCAL` state topic (`/safety/estop_state`) for late joiners

### Command Arbitration
- All velocity sources remapped through `twist_mux` — only the mux publishes the real
  `/cmd_vel`
- Priority ordering: watchdog/stop > teleop > navigation
- E-stop as a mux `lock` with a timeout, so a dead safety node also masks all sources
- Audit that `/cmd_vel` has exactly one publisher (`ros2 topic info -v`)

### SROS2 Isolation
- `ROS_SECURITY_ENABLE=true` and `ROS_SECURITY_STRATEGY=Enforce` (not Permissive) —
  unauthenticated laptops cannot join the domain
- Default-deny governance; least-privilege permissions per enclave
- Only the `safety_supervisor` enclave gets publish permission on the permit/e-stop
  topics; only the `twist_mux` enclave may publish `/cmd_vel`
- Consumers get subscribe-only access to safety topics
- Verify by attempting to spoof the permit from an enclave-less shell — it must fail

### Reset Semantics
- Latch the stop; clear only via an explicit reset service (Trigger), refused while the
  stop condition persists
- Reset restores permission, not motion — no replay of the pre-stop command
- Log engage/reset with cause for incident review

### Hardware Chain Relationship
- The software stop is a protective stop, NOT safety-rated — the hardware e-stop chain
  (safety relay, STO) remains the safety function per ISO 13849 / IEC 62061
- Software supervisor reports hardware chain status; software reset cannot clear a
  hardware stop
