# Sowbot `skills/` (branch `caatinga-dev`)

| Skill | Format | Source | Licence |
|---|---|---|---|
| [agricultural-robotics](#agricultural-robotics) | `.skill` | Agroecology Lab - This repo | MIT |
| [iso13849-plr-checklist-reviewer](#iso13849-plr-checklist-reviewer) | `.skill` | https://github.com/jherrodthomas/robotics-skills-suite (reviewer name unconfirmed) | MIT (repo) |
| [nav2-config-builder](#nav2-config-builder) | `.skill` | https://github.com/jherrodthomas/robotics-skills-suite | MIT (repo) |
| [python-coding](#python-coding) | `.skill`  | Agroecology Lab -This repo | MIT |
| [python-pro](#python-pro) | `.skill` | https://github.com/Jeffallan/claude-skills/tree/main/skills/python-pro | MIT |
| [robium-ros2](#robium-ros2) | `.skill` | https://github.com/robium-ai/robium (commit `ce94495`) | MIT |
| [robotics-software-principles](#robotics-software-principles) | `.skill` | https://github.com/arpitg1304/robotics-agent-skills (`skills/robotics-software-principles`) | Apache-2.0 (`LICENSE.txt` in archive) |
| [ros2-engineering-skills](#ros2-engineering-skills) | `.skill` | https://github.com/dbwls99706/ros2-engineering-skills (v1.7.1) | Apache-2.0 |
| [ros2-system-architecture-builder](#ros2-system-architecture-builder) | `.skill` | https://github.com/jherrodthomas/robotics-skills-suite | MIT (repo) |
| [ros2-testing](#ros2-testing) | `.skill` | https://github.com/Ekumen-OS/ros2_testing_workshop_roscon_es_25 | Apache-2.0 |
| [henki-ros2-best-practices](#henki-ros2-best-practices) | proposed skill (not in `skills/`) | https://github.com/henki-robotics/henki_ros2_best_practices (commit `2db5b52`, 2026-02-17) | Apache-2.0 |

## agricultural-robotics

- Guidance for agricultural and outdoor field robots (ROS 2 rovers, weeders, sprayers, orchard and row-crop). Not for indoor, warehouse or industrial-arm robots.
- Covers: RTK-GNSS and dual-antenna heading, sensor fusion on soil and slopes, coverage and swath planning (Fields2Cover, Nav2), crop-row following, headland turns, terrain, crop and weed detection, motor/CAN/ISOBUS firmware, field safety (e-stop, geofence, ISO 13849, ISO 18497), field testing.
- Working method: establish platform first, give starting values with what to measure, name the failure mode, no invented part numbers or APIs.
- Includes a pitfalls checklist (NavSatFix covariance, RTK fix type, antenna lever arm, magnetometer heading, frame/datum mixing, actuator timing, Nav2 defaults at field scale, Fields2Cover on contour rows, software-only safety, simulation traction).
- References: `localisation-gnss.md`, `navigation-coverage.md`, `perception.md`, `safety-and-testing.md`, `repos.md` (existence-checked 17 Oct 2026, maintenance and licence not checked), `agroecology-lab-stack.md` (Sowbot/Feldfreund snapshot, Oct 2026; file says to delete before sharing outside the team).
- Output conventions: British English, short sentences.

## iso13849-plr-checklist-reviewer

- Audits an ISO 13849-1:2023 Performance Level workbook.
- Checks: PLr derived per safety function from the S/F/P risk graph, achieved PL substantiated (Category, MTTFD, DC, CCF), CCF score ≥ 65, achieved PL ≥ PLr, written against the 2023 revision.
- Workflow: `python scripts/generate_checklist.py <workbook.xlsx> <output_checklist.xlsx>`.
- Output: 7-tab checklist (Title, General Info, Guide, Summary dashboard, Confirmation Review, Technical Assessment, Verification Assessment).
- Scripts: `generate_checklist.py`, `check_definitions.py`, `dashboard.py`, `iso13849_probe.py`, `probe_workbook.py`, `recalc.py`, `office/soffice.py`.
- `SKILL.md` lists `references/methodology.md` and `references/iso13849_checks.md`; the archive contains no `references/` folder.

## nav2-config-builder

- Generates an 11-tab XLSX Nav2 configuration workbook for ROS 2 mobile robots.
- Tabs: distro and frame baseline, global costmap, local costmap, footprint and inflation derivation, planner selection, controller selection with goal/progress checkers, behavior server and collision monitor, localization (AMCL or SLAM Toolbox), lifecycle ordering, parameter-file layout, deployment validation checklist.
- Distro baseline: Jazzy, Kilted or Lyrical, with cross-distro deltas.
- Frame IDs inherited byte-identically from `tf-tree-spec` and `urdf-model-spec` workbooks (neither is in this folder).
- Scope: configuration only, not behavior-tree design. States the criterion for each plugin choice.
- Scripts: `recalc.py`, `office/soffice.py` (LibreOffice recalculation helpers).

## python-coding

- Repo-local skill for Python changes in this repository.
- `activate_venv.sh`: creates/uses `.venv`, requires Python 3.11–3.13 (rosys).
- Rules: follow `CONTRIBUTING.md` and `AGENTS.md`, small functions, explicit error handling, update tests, run `make lint` and `make test` before commit.

## python-pro

- Python 3.11+ specialist: type hints, async/await, dataclasses, dependency injection, structured error handling, logging.
- Tooling: mypy strict, pytest with fixtures and mocking, black, ruff.
- Single `SKILL.md`, no scripts.

## robium-ros2

- Snapshot of the ROS 2 guides from robium-ai/robium. Diagnostic frameworks and failure catalogues, not tutorials.
- Triage order: find the broken layer first (environment, then downstream layers).
- Areas (each `GUIDE.md`, most with `FAILURES.md`): `ros2` (also CONCURRENCY, TURTLEBOT4, launch patterns, QoS, workspace, debugging, ament-python example), `navigation` (incl. TurtleBot3 Jazzy Gazebo), `gazebo` (sensors, ros_gz_bridge, worlds), `rviz2`, `foxglove`, `visualization`, `integration` (Dockerfile, compose, comms selection), `testing` (ROS2-AND-SIM, POLICY-EVAL).
- Not bundled from upstream: Isaac Sim/Lab, MuJoCo, LeRobot, robot data/Hugging Face, Cloud Run, RunPod, app publishing, `npx robium-ai` CLI.
- Local edits: frontmatter removed, `SKILL.md` renamed `GUIDE.md`, sibling-skill references reworded or marked "(not bundled)".

## robotics-software-principles

- Design principles for robotics modules, with BAD/GOOD Python examples.
- 12 principles: Single Responsibility, Dependency Inversion, Open-Closed, Interface Segregation, Liskov Substitution, Separation of Rates, Fail-Safe Defaults, Configuration Over Code, Idempotent Operations, Observe Everything, Composability, Graceful Degradation.
- Ends with a review checklist table (one question per principle).
- Single `SKILL.md` (896 lines) plus `LICENSE.txt`.

## ros2-engineering-skills

- ROS 2 engineering: rclcpp/rclpy, colcon/ament, launch, QoS/DDS, tf2/URDF, ros2_control, Nav2, MoveIt 2, sensors, runtime/artifact provenance, hardware safety, ROS 1 migration.
- `SKILL.md` is a task router with an operating contract (read-only reviews, resolve the environment, runtime provenance, separate permission from proof, L0–L6 verification levels).
- 28 reference files: manipulation, communication, security, testing, navigation, lifecycle-components, nodes-executors, perception, system-bringup, system-diagnostics, engineering-principles, tf2-urdf, message-types, runtime-provenance, simulation, safety-estop, hardware-interface, workspace-build, debugging, evidence-progression, deployment, multi-robot, migration-ros1, sensor-integration, launch-system, artifact-lineage, realtime, micro-ros.
- Scripts: `qos_audit.py`, `qos_checker.py`, `rosbag2_qos_checker.py`, `launch_validator.py`, `launch_supervisor.py`, `create_package.py`, `skill_validate_hook.py`, `skill_stop_hook.py`. Validators are static checks.
- Docs: `CLIENT_COMPATIBILITY.md`, `SKILL_CONTRACT.md`.
- Local modification (per `AGENTS.md`): operating-contract step 5 ("Check for existing tools before building", robodev.tools) is not upstream. Packaged as a knowledge-only install, no hooks registered.

## ros2-system-architecture-builder

- Generates an 11-tab XLSX ROS 2 system architecture workbook.
- Tabs: distro and vocabulary baseline, node inventory and responsibilities, topic/service/action catalog with QoS, lifecycle state machines, DDS QoS profile catalog, namespace/remapping/multi-robot strategy, composition and container layout, launch and bringup ordering, executor and callback-group topology, plus further tabs.
- Distro baseline: Lyrical (LTS), Jazzy (LTS), Humble (LTS); Kilted not for shipping systems; Iron end of life. Verified 2026-09-15.
- Decomposes by failure domain and lifetime; QoS chosen from data class, with reasons recorded.
- Sits above `urdf-model-spec`, `tf-tree-spec`, `nav2-config`, `behavior-tree-spec`; frame vocabulary reconciled with them.
- States the workbook is not a safety function; defers to ISO 13849 / IEC 62061 / ISO 3691-4 builders.
- Scripts are placeholders (`recalc.py`, `office/soffice.py`); no generator script.

## ros2-testing

- Writing, reviewing and running tests for ROS 2 C++ and rclpy nodes, from the Ekumen ROSCon ES 2025 workshop.
- Core rules: separate logic from ROS, inject dependencies, narrow interfaces, testing pyramid, no arbitrary sleeps, isolate tests with `ROS_DOMAIN_ID`, enforce in CI.
- Covers: GoogleTest/GoogleMock, pytest, node tests (parameters, topics, services, lifecycle), `launch_testing`, flaky-test fixes, pre-commit, GitHub Actions.
- References: `unit-and-node-tests.md`, `python-tests.md`, `integration-e2e-ci.md`.

## henki-ros2-best-practices
 
- Converted from the Henki Robotics best-practices document. `AGENTS.md` already references the upstream repo as a convention source.
- Sections: Nodes, Launch files, Parameters and config, Logging, Message interfaces, Actions and services, Code style, Performance, Executors and callback groups, Dependencies, Documentation, Testing.
- Key rules: single-responsibility nodes with logic in a separate class; XML launch files with no hardcoded parameters; YAML config under `config/`; throttled logging; reuse `common_interfaces`, custom messages in `_msgs` packages, no `std_msgs` primitives; services under 1 s, actions otherwise with enum error codes; `SingleThreadedExecutor` by default; C++ composable nodes with intra-process communication for large data; dependencies in `package.xml`, `.repos` pinned to a commit or tag; per-package README; 90-100% coverage and no arbitrary sleeps in tests.
- Modes: write and review.
 

