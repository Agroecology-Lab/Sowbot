## What the stack does

The aim of #116 is to get logic out of `NiceGuiNode` (`ui_node.py`) into layers. On `caatinga-dev` it was 4,195 lines. On `ui-116-run-plan-sim-system` (head `caa70b9`) it is 1,589.

| PR | Change |
|---|---|
| #152 Alan services | Drive, robot brain, row discovery and navigation as domain services, application services and view models |
| #153 Telemetry | Odometry and GPS state moves into `TelemetryDomainService`, `TelemetryApplicationService` and `TelemetryViewModel` |
| #156 Topo rows | Row geometry, contour planning, row building and repair become pure modules (`row_planning`, `topo_row_builder`, `plan_import`), with regression tests |
| #158 RosGateway | Subscriptions, timers and tool publishers go through `RosGateway`. The mission executor moves into `MissionDomainService`, and `ObstacleManager` and `MissionStore` use the gateway too |
| #161 Obstacles | `ObstacleViewModel`, and the obstacle cards move to `pages/obstacle_cards.py`. Saved missions sit behind `MissionViewModel` |
| #162 Topology edits | `TopologyEditService` holds the rules for dropping a node, saving F2C rows, repairing and deleting. The node methods become thin wrappers |
| #163 Sim assets | `SimAssetService` handles crop-model upload validation and writing, plus the soil zip import |
| Branch `ui-116-run-plan-sim-system` (PR number to add) | UI split into Run, Plan, Sim and System tabs. `SimPage` and `SystemPage` are classes with their cards under `pages/sim/` and `pages/system/`. `SimWorldService` rebuilds the Gazebo world. Tool processes move into `process_registry`. Map archive goes behind `TopologyViewModel`. Layer rules are enforced with import-linter in CI (six contracts). 57 test files |

#155 (optical flow in Docker) and #157 (vendored skills) are unrelated to the refactor.

The three items listed as outstanding in the 9 October comment are done: the subscription, timer and tool publishers go through `RosGateway`, the mission executor is in `MissionDomainService`, and the F2C-to-topo glue is in `TopologyEditService.stage_f2c_save`.

## Diagram as built

Solid arrows follow the dependency rule. Dashed arrows and red dashed boxes are known deviations, each listed under "Still open" below. The dotted arrows labelled "callback" and "live doc getter" are runtime callbacks.

```mermaid
flowchart TB
  subgraph P1["Phase 1: same NiceGUI ROS node (NiceGuiNode, ui_node.py, 1,589 lines)"]
    subgraph UI["View (NiceGUI)"]
      Content["content(): four tabs"]
      RunTab["Run tab<br/>_run_content (method on node, ~110 lines)"]
      PlanTab["Plan tab<br/>_plan_content (method on node, ~650 lines)"]
      SimPage["SimPage (class)"]
      SystemPage["SystemPage (class)"]
      RunCards["pages/run cards<br/>(drop node, joystick, track,<br/>row discovery, node map, sidebar)"]
      SimCards["pages/sim cards<br/>(plant placement, Gazebo, row follow,<br/>soil texture, map archive)"]
      SysCards["pages/system cards<br/>(odometry, battery, safety, ESP, GPS, tools)"]
      ObsCards["pages/obstacle_cards"]
    end

    subgraph PROC["Process helper (UI-neutral, no ROS)"]
      ProcReg["process_registry<br/>(RViz, Gazebo, explorer, grapher, neo;<br/>shared across browser sessions)"]
      Medkit["Medkit launch<br/>(subprocess in tools_card)"]
    end

    subgraph VM["ViewModel (UI state and user intent)"]
      GlobalVM["GlobalViewModel"]
      RunVM["RunViewModel<br/>(drive, row discovery, navigation)"]
      TopoVM["TopologyViewModel<br/>(mirrors the live TopoDoc, map archive)"]
      TelemetryVM["TelemetryViewModel"]
      RobotBrainVM["RobotBrainViewModel"]
      MissionVM["MissionViewModel"]
      ObstacleVM["ObstacleViewModel"]
      SimVM["SimViewModel"]
    end

    subgraph APPSVC["ApplicationService (use-case logic)"]
      DriveApp["DriveApplicationService"]
      NavApp["NavigationApplicationService"]
      RowDiscApp["RowDiscoveryApplicationService"]
      RobotBrainApp["RobotBrainApplicationService"]
      TelemetryApp["TelemetryApplicationService"]
      TopologyApp["TopologyApplicationService<br/>(save, save-as, archive, go-live, role patch)"]
      TopologyEdit["TopologyEditService<br/>(stage drop, F2C save, repair, delete)"]
      MissionApp["MissionApplicationService"]
      SimAssets["SimAssetService<br/>(crop models, soil textures; files only)"]
      SimWorld["SimWorldService<br/>(rebuild Gazebo world via worldgen.sh)"]
    end

    subgraph DOMSVC["DomainService (live state, ROS pub/sub)"]
      DriveDom["DriveDomainService"]
      NavDom["NavigationDomainService"]
      RowDiscDom["RowDiscoveryDomainService"]
      RobotBrainDom["RobotBrainDomainService"]
      TelemetryDom["TelemetryDomainService"]
      TopologyDom["TopologyDomainService<br/>(live TopoDoc, /topological_map_2,<br/>switch_topological_map client)"]
      MissionDom["MissionDomainService<br/>(mission executor, tool publishers)"]
    end

    subgraph PURE["Pure modules and packages (no ROS, no UI)"]
      RowBuilder["topo_row_builder<br/>(build and repair row nodes)"]
      RowPlanning["row_planning, plan_import"]
      F2CPkg["devkit_f2c_planner<br/>(separate package, _run_f2c)"]
    end

    subgraph INFRA["Infrastructure"]
      TopoMapStore["TopoMapStore<br/>(map file I/O)"]
      MissionStore["MissionStore<br/>(YAML or SQLite, scheduling, run recording)"]
      ObstacleManager["ObstacleManager<br/>(obstacles.yaml, /obstacles publisher)"]
      RosGateway["RosGateway<br/>(wraps the rclpy Node: publishers, subscriptions,<br/>clients, timers, clock, tf)"]
    end
  end

  Content --> RunTab
  Content --> PlanTab
  Content --> SimPage
  Content --> SystemPage
  RunTab --> RunCards
  RunTab --> ObsCards
  PlanTab --> ObsCards
  SimPage --> SimCards
  SystemPage --> SysCards

  RunCards --> RunVM
  RunCards --> GlobalVM
  RunCards --> TopoVM
  RunTab --> TelemetryVM
  PlanTab --> MissionVM
  PlanTab --> TopoVM
  SimCards --> SimVM
  SimCards --> TopoVM
  SysCards --> TelemetryVM
  SysCards --> RobotBrainVM
  ObsCards --> ObstacleVM

  SimCards -.-> ProcReg
  SysCards -.-> ProcReg
  SysCards -.-> Medkit

  RunTab -.-> TopologyEdit
  PlanTab -.-> TopologyEdit
  PlanTab -.-> RowPlanning
  PlanTab -.-> F2CPkg
  PlanTab -.-> MissionDom

  GlobalVM --> DriveApp
  RunVM --> DriveApp
  RunVM --> RowDiscApp
  RunVM --> NavApp
  TopoVM --> TopologyApp
  TelemetryVM --> TelemetryApp
  RobotBrainVM --> RobotBrainApp
  MissionVM --> MissionApp
  SimVM --> SimAssets
  SimVM --> SimWorld
  ObstacleVM -.-> ObstacleManager

  TopologyEdit --> TopologyApp
  TopologyEdit --> RowBuilder
  SimWorld --> SimAssets
  MissionApp -.->|"live doc getter"| TopologyApp

  DriveApp --> DriveDom
  NavApp --> NavDom
  RowDiscApp --> RowDiscDom
  RobotBrainApp --> RobotBrainDom
  TelemetryApp --> TelemetryDom
  TopologyApp --> TopologyDom
  TopologyApp --> TopoMapStore
  MissionApp --> MissionDom
  MissionApp --> MissionStore
  TopologyDom -.->|"doc changed callback"| TopologyApp
  TopologyApp -.->|"doc changed callback"| TopoVM

  DriveDom --> RosGateway
  NavDom --> RosGateway
  RowDiscDom --> RosGateway
  RobotBrainDom --> RosGateway
  TelemetryDom --> RosGateway
  TopologyDom --> RosGateway
  MissionDom --> RosGateway
  MissionStore --> RosGateway
  ObstacleManager --> RosGateway

  classDef view fill:#dbeafe,stroke:#1e40af,color:#111827,stroke-width:1px
  classDef todo fill:#ffffff,stroke:#b91c1c,color:#111827,stroke-width:1px,stroke-dasharray:4 3
  classDef vm fill:#dcfce7,stroke:#166534,color:#111827,stroke-width:1px
  classDef app fill:#fef9c3,stroke:#854d0e,color:#111827,stroke-width:1px
  classDef dom fill:#fae8ff,stroke:#86198f,color:#111827,stroke-width:1px
  classDef pure fill:#ffedd5,stroke:#9a3412,color:#111827,stroke-width:1px
  classDef infra fill:#e5e7eb,stroke:#374151,color:#111827,stroke-width:1px
  classDef proc fill:#f3f4f6,stroke:#374151,color:#111827,stroke-width:1px

  class Content,SimPage,SystemPage,RunCards,SimCards,SysCards,ObsCards view
  class RunTab,PlanTab todo
  class Medkit todo
  class ProcReg proc
  class GlobalVM,RunVM,TopoVM,TelemetryVM,RobotBrainVM,MissionVM,ObstacleVM,SimVM vm
  class DriveApp,NavApp,RowDiscApp,RobotBrainApp,TelemetryApp,TopologyApp,TopologyEdit,MissionApp,SimAssets,SimWorld app
  class DriveDom,NavDom,RowDiscDom,RobotBrainDom,TelemetryDom,TopologyDom,MissionDom dom
  class RowBuilder,RowPlanning,F2CPkg pure
  class TopoMapStore,MissionStore,ObstacleManager,RosGateway infra

  style P1 fill:#ffffff,stroke:#6b7280,color:#111827
  style UI fill:#eff6ff,stroke:#1e40af,color:#111827
  style PROC fill:#f9fafb,stroke:#374151,color:#111827
  style VM fill:#f0fdf4,stroke:#166534,color:#111827
  style APPSVC fill:#fefce8,stroke:#854d0e,color:#111827
  style DOMSVC fill:#fdf4ff,stroke:#86198f,color:#111827
  style PURE fill:#fff7ed,stroke:#9a3412,color:#111827
  style INFRA fill:#f3f4f6,stroke:#374151,color:#111827
```

The Run and Plan tabs are still methods on `NiceGuiNode`, which is why they are drawn as red dashed boxes. The Sim and System tabs are page classes. Because the Plan tab lives on the node, it reaches `TopologyEditService`, `row_planning`, `devkit_f2c_planner` and `MissionDomainService` directly instead of going through a ViewModel.

## Decision: drop "Facade" as a layer name

Nothing built plays that role. Its real job, a UI-neutral boundary that REST, MQTT and LoRa adapters can share, belongs to the application services. The ViewModels can't take it on, because they are NiceGUI-specific.

### Names

| Layer | Name | Rule |
|---|---|---|
| UI | Page | Layout only. Imports ViewModels only (enforced by import-linter). Tool processes are the one exception: cards start them through `process_registry`, and Medkit still uses `subprocess` in `tools_card` |
| UI state | ViewModel | UI state and user intents. It is the NiceGUI adapter, and the only place presentation formatting happens |
| Boundary | Application service | The "Robot API". Use cases, plain dataclasses in and out, no UI or rclpy types. Phase 2 adapters call this layer |
| Live state | Domain service | Holds live state (TopoDoc, mission state). Talks to ROS only through RosGateway |
| Persistence | Store | TopoMapStore, MissionStore. Owned by application services. `TopoMapStore` sits in the `domain_services` package for now |
| Algorithms | Pure module | row_planning, topo_row_builder, plan_import. No state, no ROS. F2C is the separate `devkit_f2c_planner` package |
| ROS | RosGateway | Creates publishers, subscriptions, clients and timers. Five modules still import `rclpy.qos` directly (see below) |
| Processes | process_registry | Registry of long-running tool processes (RViz, Gazebo, explorer, grapher, neo), shared by every browser session. No NiceGUI or ROS imports |
| Phase 2 edge | Adapter | RestAdapter, MqttAdapter, ReticulumAdapter. Each is thin and calls application services. Not built |
| Phase 2 central | Fleet Gateway | Renamed from "Central Gateway" so "gateway" means one thing only. Not built |

**Dependency rule:** Page → ViewModel → Application service → Domain service / Store / Pure module → RosGateway. Nothing points upward or skips a layer.

import-linter enforces this in CI (`make lint-imports`, `.importlinter`) with six contracts:

1. Pages import only ViewModels.
2. Pages do not spawn processes.
3. ViewModels do not reach domain services, stores or ROS.
4. Services are UI-neutral.
5. Domain services do not import application services.
6. Only RosGateway touches rclpy.

Each exception is listed in `.importlinter` with a `TODO(#116)` and the check fails if one becomes stale.

### How the old Facade duties are split

- ROS message to plain type: `RosGateway` and the domain services.
- Plain type to display format: ViewModel.
- Plain type to wire format (JSON, protobuf, Reticulum frames): Adapter (Phase 2, not built).

### Consequences

- **The persistence objection no longer applies.** achurichi's rule was "no state or persistence in the Facade". That was about a layer that doesn't exist now. Stores sit in the application layer, as the build already has them.
- **Application services must stay thin orchestration.** Planning lives in pure modules and the executor in `MissionDomainService`. Otherwise the monolith concern comes straight back.
- **`MissionStore` no longer bypasses the layers.** In the 15 August diagram `MissionRunner` and `MissionStore` bypassed the Facade. `MissionViewModel` now goes through `MissionApplicationService`, which owns the store and the domain service.

## Proposed in #116 versus built

| Proposed | Built |
|---|---|
| Facade layer (comment of 15 and 20 August: `NavFacade`, `MissionFacade`, `TopologyFacade`, `SystemFacade`) | Dropped. `ApplicationService` is the boundary (comment of 9 October) |
| Three ViewModels: `NavViewModel`, `MissionViewModel`, `SystemViewModel`, plus `GlobalViewModel` | Eight: `Global`, `Run`, `Topology`, `Telemetry`, `RobotBrain`, `Mission`, `Obstacle`, `Sim` |
| Pages: Nav, Mission, System | Four tabs: Run, Plan, Sim, System. Sim and System are page classes. Run and Plan are still node methods |
| `NavigationService`, `MissionService`, `TopologyService`, `SystemService` | Drive, Navigation, RowDiscovery, RobotBrain, Telemetry, Topology, TopologyEdit and Mission application services, each with a domain service, plus `SimAssetService` and `SimWorldService` (no ROS) |
| `PlanningService (F2C)` / `PlanningDomainService` | No such service. F2C is the `devkit_f2c_planner` package, called from the Plan tab. Row geometry, row building and plan import are pure modules. Edit rules are in `TopologyEditService` |
| `TopoRepo` | `TopoMapStore`, owned by `TopologyApplicationService` |
| `ROSGateway` as the ROS clients layer | `RosGateway` wraps the rclpy node. All seven domain services, `MissionStore` and `ObstacleManager` use it |
| "All UI contact points go through the Facade" | True for drive, navigation, row discovery, robot brain, telemetry, topology and missions. Not true for obstacles, process control and the Plan tab (see below) |
| Enforce the dependency rule | Done: import-linter in CI |
| Phase 2: Edge API (REST, MQTT, VDA 5050), Fleet Gateway, standalone UI | Not started. No adapters in the code |

## Still open

Tracked in #116 and `.importlinter`:

- **Run and Plan tabs** are still methods on `NiceGuiNode`. `_plan_content` is about 650 lines and holds the F2C call, plan import and mission queue UI.
- **Mission start glue**: `_run_mission` and `_send_goal_sync` are still on the node, around `MissionDomainService.start`.
- **Node leftovers**: F2C session state (`_f2c_*`), the `_persist_and_reload` thread wrapper, and the `/current_node` subscription with its `rclpy.qos` import.
- **Obstacles**: `ObstacleViewModel` calls `ObstacleManager` directly. An `ObstacleApplicationService` is needed.
- **Medkit**: still started with `subprocess` in `pages/system/tools_card.py`.
- **`rclpy.qos` imports**: `row_discovery_domain_service`, `telemetry_domain_service`, `topology_domain_service`, `obstacles` and `qos` still import `rclpy`. The QoS profiles should move into `RosGateway`.
- **Process control**: the other tool cards (Gazebo, row follow, RViz, explorer, grapher) start processes through `process_registry` from the page. A ViewModel and application service would complete the rule.
