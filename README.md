# Sowbot (ROS 2 stack)
<!-- CI & Test Status -->
[![Industrial CI](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/industrial_ci.yml/badge.svg?branch=caatinga-dev)](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/industrial_ci.yml?query=branch%3Acaatinga-dev)
[![Run Tests](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/test.yml/badge.svg)](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/test.yml)
[![CodeQL](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/github-code-scanning/codeql)
[![CodeQL Code Quality](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/github-code-quality/codeql/badge.svg)](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/github-code-quality/codeql)
[![Docs](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/documentation.yml/badge.svg)](https://github.com/Agroecology-Lab/Sowbot/actions/workflows/documentation.yml)

<!-- Code Coverage -->
[![Codecov](https://codecov.io/gh/Agroecology-Lab/Sowbot/branch/caatinga-dev/graph/badge.svg)](https://codecov.io/gh/Agroecology-Lab/Sowbot/branch/caatinga-dev)

An open-source, containerised ROS2 Jazzy stack for autonomous agricultural robotics.
This repository provides the drivers and orchestration for the Sowbot platform, featuring RTK-GNSS localisation and STM32-based hardware control.

[Quick start](#quick-start)

[Docs](https://agroecology-lab.github.io/Sowbot_Data/python_api.html)
```
+---------------------------------------------------------+
|                ROS 2 DUAL SBC Open Core                 |
| +----------------------+     +----------------------+   |
| |       NEO SBC        | Gig |      LIMBIC SBC      |   |
| | - Camera Streams     | Eth | - NiceGUI Dashboard  |   |
| |   (YOLO / ExG)       |<===>| - Nav2/LCAS TopoNav  |   |
| | - Visual Servoing    |     | - FusionCore UKF     |   |
| | - Crop Processing    |     | - devkit_mavlink     |   |
| | - High-Throughput    |     |                      |   |
| |   Perception         |     |                      |   |
| +----------------------+     +----------------------+   |
+---------------------------v-----------------------------+
                            |
                    Serial MAVLink (UART)
                            |
+---------------------------v-----------------------------+
|               ARDUPILOT FLIGHT CONTROLLER               |
|    LEVIA-H7 running ArduRover, GUIDED mode (bring-up)   |
|    SUPERVISORY: not the rated stop function             |
|---------------------------------------------------------|
| - Motion execution       - EKF3, sole estimator        |
| - Geofence (GUIDED)         (FusionCore: not run)      |
| - Failsafes: GCS/GPS     - Arming state machine         |
|   loss, EKF variance     - Pre-arm checks               |
| - Dual F9P RTK (UART)                                   |
+---------------------------+-----------------------------+
                            |
             DroneCAN & SimpleCan (Lua)/ CAN bus
                            |
+---------------------------v-----------------------------+
|         MOTOR CONTROLLERS (48V traction)               |
|---------------------------------------------------------|
| Differential-drive tracked platform                     |
| Traction power passes through the SW180 contactors      |
| of the hardwired safety loop below                      |
+---------------------------------------------------------+

+---------------------------------------------------------+
|     HARDWIRED 24V SAFETY LOOP (rated stop function)     |
| E-stops, bumpers, Inxpect radar + C203A -> 2x SW180     |
| in series on the 48V bus. Independent of ROS,           |
| ArduPilot and the SBCs.                                 |
+---------------------------------------------------------+
```
[Full architectural diagram](https://github.com/Agroecology-Lab/Sowbot/blob/caatinga-dev/docs/images/ui/architecture.md)

LLM Generated diagram:

[![Alternate LLM generated architectural diagram](https://gitdiagram.com/agroecology-lab/feldfreund_devkit_ros/diagram.png)](https://gitdiagram.com/agroecology-lab/sowbot?utm_source=readme&utm_medium=picture)



Reference open hardware stack(s) under development at [Sowbot.co.uk](https://sowbot.co.uk/hardware) Contact: [sowbot.co.uk/contact](https://sowbot.co.uk/contact/)

Development is led by the <a href="https://agroecologylab.org.uk" target="_blank">Agroecology Lab</a> building on the core developed by <a href="https://zauberzeug.com/" target="_blank">Zauberzeug</a>.

We have a [Online version of swath planner](https://agroecology-lab.github.io/Sowbot/)

**Collaborators welcome.**
See [CONTRIBUTING.md](CONTRIBUTING.md).

**Milestone — multi-row mission following validated in Gazebo:**

[![Multi-row mission following in Gazebo](https://img.youtube.com/vi/A0CVNcp19vU/0.jpg)](https://www.youtube.com/watch?v=A0CVNcp19vU)

(click above image to play video)

## Sowbot Roadmap

Related [Safety Roadmap](https://github.com/Agroecology-Lab/Sowbot/blob/caatinga-dev/safety_roadmap.md)

| # | Feature | Status | TRL |
|---|---------|--------|-----|
| F1–F5 | Containerised deployment, stable device addressing (`fixusb.py`), NiceGUI dashboard, dual F9P driver, diagnostics TUI | Done, bench and sim only | 4 |
| M1 | Agri Open Core platform integration (Topo nav, Sentor) | Done | 4 |
| M2 | Topological navigation + Nav2, multi-row missions | Sim only. Blocked: hardware field validation | 4 |
| M3 | Open-field row-crop planning (fields2cover, [web planner](https://agroecology-lab.github.io/Sowbot/)) | Sim only. Blocked: field survey, obstacle costmap layer | 4 |
| M4 | RTK-GNSS localisation | Blocked: hardware test | 3 |
| M5 | Dual-SBC ROS 2 stack | Implemented, no hardware test | 3 |
| M6 | Gazebo simulation | Done. Does not model track slip | 4 |
| M7 | Sentor safety monitoring | Implemented, wired into `devkit.launch.py` for real hardware. Not yet run on hardware; battery cutoff unconfirmed | 3 |
| M8 | Visual crop-row navigation | Tested & iterated on lettuce rows | 4 |
| M9 | ArduPilot on LEVIA-H7 controller path | Bring-up. Never run against a flight controller | 2 |
| M10 | MeshCore robot-to-robot avoidance | Sim only, not integrated, not a safety function | 2 |
| M11 | LiteFarm interface (optional) | Implemented, unit-tested without hardware | 3 |
| M12 | Remote missions | Spec only | 2 |
| P1 | Phase 1: dev platform (current) | In progress, supervised use only. Blocked: O7, O8, O14, O16 | 3 |
| P2 | Phase 2: OEM modular subsystems | Research | 2 |
| P3 | Phase 3: commercial sale to farmers | Planned. ISO 18497 / 13849 certification not scoped | 1 |
| D2 | Caatinga biome dataset | Active | 4 |

<sub>TRL (1–9, ESA/NASA scale): 1–2 concept, 3 proof of concept, 4 validated in lab or simulation, 5 validated in a relevant environment, 6 demonstrated in a relevant environment. Self-assessed. Nothing is scored above 4 until field results are published (see below).</sub>

### Field results

None published yet.
Simulation results are not evidence for the tracked hardware (Gazebo does not model slip).
Once hardware runs start, each will report: cross-track error (RMS and maximum), share of time in RTK fixed, interventions per hour, and measured stopping distance.
The progression from bench to supervised rows, with exit criteria for each step, is in [docs/field-validation.md](docs/field-validation.md).

### Collaboration

This project is built on and aims to maintain upstream compatibility with [zauberzeug/feldfreund\_devkit\_ros](https://github.com/zauberzeug/feldfreund_devkit_ros).

High Level navigation is developed from the work of [Lincoln Centre for Autonomous Systems (LCAS)](https://lcas.lincoln.ac.uk) as part of the [Agri-OpenCore](https://agri-opencore.org) open ROS 2 ecosystem for agricultural robotic

Simulation configuration is partially derived from work by [caatingarobotics](https://github.com/joaodemouragy-hash/caatingarobotics),


# ⚠️ CRITICAL SAFETY WARNING:

 This software is under active development and may be broken at any given moment.
For a stable reference implementation see the upstream Zauberzeug project.

**THIS SOFTWARE COULD CONTROL PHYSICAL HARDWARE CAPABLE OF PRODUCING SIGNIFICANT KINETIC FORCE.**

1. **EXPERIMENTAL STATUS**: This branch ('caatinga-dev') contains experimental code generated and
   refined with AI assistance.
   It has NOT undergone full-scale field validation.
2. **STATUTORY NOTICE (UK)**: Usage of this software is at the user's sole risk.
   While
   standard open-source licenses apply, users are reminded that operating agricultural
   robotics requires a professional duty of care.
3. **MANDATORY HARDWARE SAFETY**: Under no circumstances should this software be used
   to control a robot of any size without an independent safety subsystem.
   Software-based stops (such as /estop/soft) are NOT a substitute for
   Category 0 or 1 hardware safety stops.
4. **CURRENT STATUS**: the hardwired 24V stop loop is still being built and the radar is not yet validated
   on the vehicle (see [safety_roadmap.md](safety_roadmap.md), O7, O8, O14).
   Operation requires the hard-wired 24V loop, including the physical E-stop path, to be built and tested, or a separately validated independent stop method; otherwise operation is prohibited.
   Until O7, O8 and O14 close, any permitted operation is supervised-only, with a competent person within reach of a physical E-stop (or the separately validated stop control), nobody working near it, and no safety claim made.
5. **NO LIABILITY**: To the extent permitted by the laws of England and Wales, the
   contributors exclude all liability for property damage, crop loss, or indirect
   consequential damages.

### Health Warning

This repo may contain traces of LLM slop.
We've done our best to mitigate this.
If you are allergic to slop, please help us refactor.

## Quick Start

### Supported SBC configuration

- [Avaota A1](https://pine64.com/product/yuzuki-avaota-a1-single-board-computer-4gb-32gb/)
- [Armbian](https://armbian.com/boards/avaota-a1) (note flash with official Armbian flasher)

### 0. Install dependencies

#### Linux
- [Git](https://github.com/git-guides/install-git)
- [Docker](https://docs.docker.com/engine/install/debian/#install-using-the-repository)
- ```sudo apt install python3-serial setserial v4l-utils```
- ```sudo apt install -y pipx && pipx install 'glances[web]' && pipx ensurepath && (nohup ~/.local/bin/glances -w --bind 127.0.0.1 > /tmp/glances.log 2>&1 < /dev/null &)```

#### Mac
- xcode-select --install
- [Git](https://github.com/git-guides/install-git)
- [Docker](https://docs.docker.com/engine/install/)
- & launch Gazebo in a browser from the WebUI.

#### Windows?
- [Git](https://github.com/git-guides/install-git)
- [Docker](https://docs.docker.com/engine/install/)
- Untested!

### 1. Clone the Repository
Open a terminal on your host machine and download the workspace:
```bash
git clone -b caatinga-dev https://github.com/Agroecology-Lab/Sowbot.git
cd Sowbot
```


### 2. Build & Launch
Use the management script to build the ROS 2 workspace and launch the robot stack.
This script automatically handles hardware discovery and port permissions:
```bash
./manage.py full-build
xhost +local:docker
./manage.py
```
Access http://localhost to access the WebUI

On a real robot with the LEVIA-H7 flight controller, create `.env` from `.env.example` first. See [Real robot bring-up](https://github.com/Agroecology-Lab/Sowbot/blob/caatinga-dev/src/devkit_mavlink_bridge/mavlink.md#real-robot-bring-up)

**The dashboard has a joystick and a soft e-stop and listens on port 80 of the robot.**
Set `SOWBOT_UI_PASSWORD` in `.env` to require a password (HTTP Basic, user name ignored).
Without it, anyone on the robot's network can drive it.
Basic auth is not encrypted, so also keep the robot's Wi-Fi private.
The container runs privileged on the host network.



#### Linux

If you're getting this error:
```
docker: Error response from daemon: could not select device driver "" with capabilities: [[gpu]]
```
You'll need to install the [Nvidia Container runtime](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

#### Gazebo

If you'd like to use Gazebo then add a +sim argument to your build instruction

```bash
./manage.py full-build +sim
xhost +local:docker
./manage.py
```

## Management & Tools

### manage.py
The primary entry point for the system.
While it runs the full stack by default, it supports several optional arguments for development:

| Command | Logic / Argument | Resulting Action |
|:---|:---|:---|
| `./manage.py` | (No arguments) | Runs `run_runtime()` immediately using live volumes.|
| `./manage.py build` | `build` | local-only, fully cached including external clones |
| `./manage.py build +pull` | `build` | Re-clones the 10 external repos, keeps apt/pip/local layers cached |
| `./manage.py build +pull +sim` | `build ` | Same, plus INSTALL_SIM=true |
| `./manage.py full-build` | `full-build` | Runs `run_build(full=True)`. Cleans system & Re-installs all system dependencies. |
| `./manage.py neo` | `neo` | Runs `neo`. Runs only the line following code for the second 'neo'(cortex) perception SBC. Then check `http://localhost:8081` (web_video_server; 8080 is the Medkit gateway) |
| `./manage.py neo-tsm` | `neo-tsm` | Runs `neo-tsm`. Runs experimental line following informed by [Vision based Crop Row Navigation under Varying Field Conditions in Arable Fields - Rajitha de Silva1, Grzegorz Cielniak2 and Junfeng Gao](https://arxiv.org/pdf/2209.14003). Then check `http://localhost:8081` (web_video_server; 8080 is the Medkit gateway) |
| `./manage.py pull-caatinga` | `pulls & builds vision pipeline` | Requires the container to already be running |


### One-command sim launch (TMuLE)

The simulation launch accepts `use_camera:=false` to disable the simulated camera:

```bash
ros2 launch devkit_bringup sowbot_sim.launch.py use_camera:=false
```

Use `camera_width`, `camera_height`, and `camera_rate` to override the default 320×240 image at 10 Hz.
These arguments also work with `ros2 launch devkit_simulation sim.launch.py`.

Whilst you can launch Gazebo and other tools from the webui, some may prefer the terminal, this makes terminal use a bit easier.

[TMuLE](https://github.com/marc-hanheide/TMuLE) brings the whole row-following sim up in a single `tmux` session — one window per process — instead of running the three launch steps by hand in separate terminals:

```bash
tmule -c tmule/row_follow_sim.yaml launch
tmux attach -t row_follow_sim
```

That starts `./manage.py --sim` (nav stack + Nav2 + UI), Gazebo (`sowbot_sim.launch.py`) and the crop-row CV node (`crop_row_nav.launch.py`), each in its own tmux window.
`launch` returns immediately and leaves the session detached, so attach to watch the panes come up (and to type the `nav_stack` sudo password).

Stop the stack with `tmule -c tmule/row_follow_sim.yaml stop`; re-attach later with `tmux attach -t row_follow_sim`.

> If a runtime container is **already running**, the `nav_stack` window will fail on a `docker run` name collision.
> Either `docker stop sowbot_runtime` first, or launch just the windows that reuse the container: `tmule -c tmule/row_follow_sim.yaml launch -w gazebo`.

#### tmux basics

The stack runs in a *detached* tmux session named `row_follow_sim`, so closing your terminal does not kill it — **detaching is not stopping**.
The session holds one window per sub-system (plus a stray `0: bash` that tmux always creates):

```
0: bash   1: nav_stack   2: gazebo   3: crop_row
```

Every shortcut starts with the prefix `Ctrl-b` — press and release it, then the next key:

| Keys | Resulting Action |
|:---|:---|
| `Ctrl-b` `d` | Detach — leaves the whole stack running in the background |
| `Ctrl-b` `w` | Interactive window picker (easiest way to move around) |
| `Ctrl-b` `1` / `2` / `3` | Jump straight to `nav_stack` / `gazebo` / `crop_row` |
| `Ctrl-b` `n` / `p` | Next / previous window |
| `Ctrl-b` `[` | Scroll back through ROS log output — arrows/PgUp, `q` to exit |
| `Ctrl-b` `z` | Zoom the current pane fullscreen (press again to unzoom) |
| `Ctrl-b` `?` | List every binding |

```bash
tmux ls                              # list sessions
tmux attach -t row_follow_sim        # attach
tmux kill-session -t row_follow_sim  # nuke the session
```

Scrolling back (`Ctrl-b` `[`) is the one you'll reach for most — it's how you read ROS output that has already scrolled past.
For mouse-wheel scrolling instead, add `set -g mouse on` to `~/.tmux.conf`.

Scenario configs live in [`tmule/`](tmule/) — see [`tmule/README.md`](tmule/README.md) to tweak launch arguments or add your own scenario.

### Interactive Shell
To enter the running container for debugging or manual ROS 2 commands:
```bash
./tools/login.sh
```

### Diagnostics
If hardware is connected but topics are not flowing, run the diagnostic tool from inside the container:

#### After running ./tools/login.sh
```bash
python3 tools/agbot-diagnostic.py
```

![TUI Status.](https://raw.githubusercontent.com/Agroecology-Lab/Open_agbot_devkit_ros/refs/heads/main/assets/Screenshot%20From%202026-01-21%2018-07-45.png)

You can also make it verbose with:
```bash
python3 tools/agbot-diagnostic.py full
```

## Sketch of MVP 2026 architecture

### 1. The Flight Controller (RT Microcontroller)
* **Hardware:** [LEVIA-H7](https://github.com/piecol/LEVIA-H7) (STM32H743).
  Unvalidated, in bring-up.
* **Software:** ArduPilot Rover, GUIDED mode.
* **Role:** Motion execution and supervisory failsafes (geofence, GCS/GPS loss, EKF variance).
* **I/O:** MAVLink serial from the Limbic SBC via `devkit_mavlink_bridge`; CAN for motor drivers (driver choice open).
* **Not the rated stop function.**
  Stopping on a bumper, radar or E-stop is done by the hardwired 24V loop, independent of this controller (see [safety_roadmap.md](safety_roadmap.md)).
* **Legacy:** ESP32 + Lizard DSL (3.3V UART via the `teleop_lizard` bridge, see [docs/lizard-firmware-setup.md](docs/lizard-firmware-setup.md)).
  Kept for existing hardware, no new features.

### 2. The Limbic System (Executive)
* **Hardware:** Avaota A1 #1 (Allwinner T527).
* **Software:** ROS 2 Jazzy + `topological_navigation` (`main`).
* **Role:** Navigation Executive.
* **Function:** Runs the Nav2-based Topological Navigation stack.
  Manages `NavigateToPose` actions and Action on Condition (AOC) logic.
* **I/O:** Connects to u-blox via USB/UART using `ublox_dgnss` node.
  Translates graph goals into velocity commands for the flight controller.

**<1GbE interconnect between 2&3>**

### 3. The Neo (Perception)
* **Hardware:** Avaota A1 #2 (Allwinner T527 + NPU).
* **Software:** Dockerised ROS 2 Jazzy.
* **Role:** Asynchronous Perception.
* **Function:** NPU-accelerated inference (YOLO/Object tracking) and sensor fusion.
* **Connectivity:** Native Zenoh integration via `rmw_zenoh_cpp`.
  Publishes environment states and "Conditions" to the Zenoh network.

                          |

## Sketch of possible eventual ~2027 architecture

Research only (TRL 1 to 2 in the roadmap above), not a commitment.
It would replace ArduPilot on the controller, which is the current path.

### 1. The Real-Time Controller (Hardware Abstraction)
* **Hardware:** [STM32 H7 MCU](https://oshwhub.com/6676a/stm32h745zit6_core).
* **Software:** copper-rs.
* **Role:** Hard Real-Time Execution.
* **Function:** Manages motor PID loops and hardware-level safety interlocks.
* **I/O:** Canbus

### 2. The Limbic System (Executive)
* **Hardware:** Avaota A1 #1 (Allwinner T527).
* **Software:** RT kernel, Buildroot `copper-rs`
* **Role:** Deterministic Executive.
* **Function:** UBLOX sensors, Executes Action on Condition (AOC) logic for topological navigation.
* **Data Entry:** Directly consumes Zenoh keys from the Neo board to trigger mission state transitions and motion planning.

| Core(s)   | Role                | Allocation Strategy                                                                 |
| :-------- | :------------------ | :---------------------------------------------------------------------------------- |
| Core 0    | OS / I/O            | Handles kernel house-keeping, SSH, and the 1GbE driver interrupts.                  |
| Core 1    | Zenoh / Neo-link    | Dedicated to the Zenoh router and serializing incoming "Nice-to-Have" data.         |
| Cores 2-6 | The Pilot (Nav)     | This is where the RTK EKF, Path Planner, and Task Graph live.                       |
| Core 7    | The Bridge (controller) | Dedicated to SocketCAN and the high-frequency heartbeat to the STM32.            |


**<1GbE interconnect between 2&3>**

### 3. The Neo (Perception)
* **Hardware:** Avaota A1 #2 (Allwinner T527 + NPU).
* **Software:** Dockerised ROS 2 Jazzy & Dockerised CV packages
* **Role:** Asynchronous Perception.
* **Function:** NPU-accelerated inference (YOLO/Object tracking) and sensor fusion.
* **Connectivity:** Native Zenoh integration via `rmw_zenoh_cpp`.
  Publishes environment states and "Conditions" to the Zenoh network.


## Licenses & papers

# Sowbot — Dependency Licence Audit

| Component | Source | Licence | Commercial use | Academic Paper | Notes |
|---|---|---|---|---|---|
| **Sowbot** (this repo, formerly feldfreund_devkit_ros) | Agroecology-Lab/Sowbot | MIT (©Zauberzeug GmbH & Agroecology Lab) | ✅ | *None (Local Project)* | Derivative of upstream field-friend; retain Zauberzeug notice |
| **devkit_driver** | local | MIT (©ATB) | ✅ | *None (Utility Driver)* | Retain ATB copyright notice |
| **devkit_ui** | local | MIT (©Agroecology Lab) | ✅ | *None (UI Extension)* | |
| **devkit_bringup** | local | MIT | ✅ | *None (Config/Launch)* | |
| **sowbot_row_follow** | caatingarobotics | BSD-2-Clause | ✅ | [de Silva et al., 2024](https://arxiv.org/abs/2209.14003) | Triangle Scan Method (TSM) for visual crop row navigation; ©PRBonn + ©Agroecology Lab |
| **caatingarobotics** *(devkit_simulation, caatinga_nav, caatinga_vision)* | [samuk/caatingarobotics](https://github.com/samuk/caatingarobotics) (cloned in the Dockerfile); upstream work by [joaodemouragy-hash/caatingarobotics](https://github.com/joaodemouragy-hash/caatingarobotics) | Apache-2.0 | ✅ | *None (Fork Infrastructure)* | row_follow is the BSD-2-Clause exception, above |
| **topological_navigation** | LCAS (`main`) | Apache-2.0 (©LCAS) | ✅ | [Fentanes et al., 2015](https://www.researchgate.net/publication/282752920_Now_or_later_Predicting_and_maximising_success_of_navigation_actions_from_long-term_experience) | High-level planner; derived from the EU STRANDS long-term autonomy project framework |
| **fusioncore** | manankharwar | Apache-2.0 | ✅ | [Kharwar, 2026](https://arxiv.org/abs/2605.25239) | GNSS fusion; patched in-build; retain NOTICE if present |
| **Forest3D** | unitsSpaceLab | GPL (©UNITS Space Lab) | Do not ship in final product | [Cottiga,S., Bourr, K., & Seriani, S. (2026)](https://kbourr.com/#publications)| Sim-only — 3D forestry /agriculture simulation environment |
| **ublox_dgnss** | aussierobots | Apache-2.0 | ✅ | *None (Hardware Driver)* | GNSS driver |
| **sentor** | LCAS (fork of francescodelduchetto/sentor) | MIT | ✅ | *None (Monitoring Tool)* | Topic- and node-monitoring health node |
| **ros2graph_explorer** | nilseuropa | BSD-3-Clause | ✅ | *None (Debug Tool)* | Dev/debug graph inspector |
| **optical_flow_ros** | adityakamath | Apache-2.0 | ✅ | *None (Hardware Driver)* | PMW3901 / PAA5100 optical flow sensor driver |
| **Fields2Cover v2.0.0** | Fields2Cover | BSD-3-Clause | ✅ | [Mier et al., 2023](https://doi.org/10.1109/LRA.2023.3248439) | Built from source; pulls OR-tools (Apache-2.0) + GDAL (MIT) |
| **lizard** | Agroecology-Lab | MIT (©Zauberzeug GmbH) | ✅ | *None (Firmware Tool)* | ESP32 tooling (legacy controller path); retain Zauberzeug notice |
| **YOLOX 0.3.0** + yolox_nano weights | Megvii | Apache-2.0 | ✅ | [Ge et al., 2021](https://arxiv.org/abs/2107.08430) | Confirm weights terms for commercial use |
| **PyTorch (CPU)** | Meta | BSD-3-Clause | ✅ | [Paszke et al., 2019](https://papers.nips.cc/paper/9015-pytorch-an-imperative-style-high-performance-deep-learning-library) | Core machine learning runtime engine |

## Upstream documentation

The original Feldfreund DevKit text (components, UI, camera and AXIS setup) is kept in [docs/upstream-feldfreund-readme.md](docs/upstream-feldfreund-readme.md).
Parts of it do not apply to Sowbot, see the note at the top of that file.
