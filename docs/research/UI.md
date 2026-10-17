## What the stack does

The aim of #116 is to get logic out of `NiceGuiNode` (`ui_node.py`) into layers.
On `caatinga-dev` it was 4,195 lines.
On `ui-116-run-plan-sim-system` (head `caa70b9`) it was 1,589.
On `ui-116-Plan-service-fixes` it was 838.
On `caatinga-dev` at `c69b649` (after #174 and #175) it is 360.
With the #168 and #169 change it is 354, and `NiceGuiNode` no longer subclasses `rclpy.node.Node`.

<table>
<tr><th>PR</th><th>Change</th></tr>
<tr><td>#152 Alan services</td><td>Drive, robot brain, row discovery and navigation as domain services, application services and view models</td></tr>
<tr><td>#153 Telemetry</td><td>Odometry and GPS state moves into <code>TelemetryDomainService</code>, <code>TelemetryApplicationService</code> and <code>TelemetryViewModel</code></td></tr>
<tr><td>#156 Topo rows</td><td>Row geometry, contour planning, row building and repair become pure modules (<code>row_planning</code>, <code>topo_row_builder</code>, <code>plan_import</code>), with regression tests</td></tr>
<tr><td>#158 RosGateway</td><td>Subscriptions, timers and tool publishers go through <code>RosGateway</code>.
The mission executor moves into <code>MissionDomainService</code>, and <code>ObstacleManager</code> and <code>MissionStore</code> use the gateway too</td></tr>
<tr><td>#161 Obstacles</td><td><code>ObstacleViewModel</code>, and the obstacle cards move to <code>pages/obstacle_cards.py</code>.
Saved missions sit behind <code>MissionViewModel</code></td></tr>
<tr><td>#162 Topology edits</td><td><code>TopologyEditService</code> holds the rules for dropping a node, saving F2C rows, repairing and deleting.
The node methods become thin wrappers</td></tr>
<tr><td>#163 Sim assets</td><td><code>SimAssetService</code> handles crop-model upload validation and writing, plus the soil zip import</td></tr>
<tr><td>Branch <code>ui-116-run-plan-sim-system</code> (PR number to add)</td><td>UI split into Run, Plan, Sim and System tabs.
<code>SimPage</code> and <code>SystemPage</code> are classes with their cards under <code>pages/sim/</code> and <code>pages/system/</code>.
<code>SimWorldService</code> rebuilds the Gazebo world.
Tool processes move into <code>process_registry</code>.
Map archive goes behind <code>TopologyViewModel</code>.
Layer rules are enforced with import-linter in CI (six contracts).
57 test files</td></tr>
<tr><td>Branch <code>ui-116-Plan-service-fixes</code> (PR number to add)</td><td><code>PlanApplicationService</code> plans rows, imports plans and saves them.
It returns plain data and keeps no draft.
<code>PlanViewModel</code> words the status and opens a <code>PlanPageState</code> per browser session (boundary, draft, status, button state).
The Plan tab is <code>PlanPage</code>, with its cards under <code>pages/plan/</code>: boundary map, controls, repair dialog, mission queue, mission store.
A planning result that arrives after Clear is discarded.
A seventh import-linter contract keeps the node, pages and view models off the F2C planner, <code>row_planning</code> and <code>plan_import</code></td></tr>
<tr><td>#175 NavPage (#164, #167)</td><td>The Run tab is <code>NavPage</code>.
The node wrappers move into view models: <code>persist_and_reload</code> and <code>patch_node_role</code> are in <code>TopologyApplicationService</code>, and <code>MapAuthoringViewModel</code> holds the map-authoring actions</td></tr>
<tr><td>#168, #169 (PR number to add)</td><td>Topic QoS is declared as <code>QosSpec</code> values in <code>qos.py</code>, which has no rclpy import, and <code>RosGateway</code> converts them to <code>QoSProfile</code>.
No module outside <code>RosGateway</code> imports <code>rclpy</code>, and the five ignores in the <code>rclpy-via-gateway</code> contract are gone.
<code>NiceGuiNode</code> is a plain class built on a <code>RosGateway</code>; <code>ros_main</code> creates the <code>Node</code>.
The <code>sim</code> parameter is read through <code>RosGateway.get_bool_parameter</code></td></tr>
</table>

#155 (optical flow in Docker) and #157 (vendored skills) are unrelated to the refactor.

The three items listed as outstanding in the 9 October comment are done: the subscription, timer and tool publishers go through `RosGateway`, the mission executor is in `MissionDomainService`, and the F2C-to-topo glue is in `TopologyEditService.stage_f2c_save`.

## Diagram as built

Solid arrows follow the dependency rule.
Dashed arrows and red dashed boxes are known deviations, each listed under "Still open" below.
The dotted arrows labelled "callback" and "live doc getter" are runtime callbacks.

```mermaid
flowchart TB
  subgraph P1["Phase 1: same NiceGUI process and ROS node (NiceGuiNode, ui_node.py, 354 lines)"]
    subgraph UI["View (NiceGUI)"]
      Content["content(): four tabs"]
      NavPage["NavPage (class)"]
      PlanPage["PlanPage (class)"]
      SimPage["SimPage (class)"]
      SystemPage["SystemPage (class)"]
      RunCards["pages/run cards<br/>(drop node, joystick, track,<br/>row discovery, node map, sidebar)"]
      PlanCards["pages/plan cards<br/>(boundary map, controls, repair dialog,<br/>mission queue, mission store)"]
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
      AuthoringVM["MapAuthoringViewModel<br/>(drop node, F2C save, repair, delete row)"]
      TopoVM["TopologyViewModel<br/>(mirrors the live TopoDoc, map archive)"]
      TelemetryVM["TelemetryViewModel"]
      RobotBrainVM["RobotBrainViewModel"]
      PlanVM["PlanViewModel<br/>(plan status wording, PlanPageState per session)"]
      MissionVM["MissionViewModel<br/>(saved missions, MissionQueue per session)"]
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
      PlanApp["PlanApplicationService<br/>(plan rows, import plan, save rows; keeps no draft)"]
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
      F2CPkg["devkit_f2c_planner<br/>(separate package, run_f2c)"]
    end

    subgraph INFRA["Infrastructure"]
      TopoMapStore["TopoMapStore<br/>(map file I/O)"]
      MissionStore["MissionStore<br/>(YAML or SQLite, scheduling, run recording)"]
      ObstacleManager["ObstacleManager<br/>(obstacles.yaml, /obstacles publisher)"]
      RosGateway["RosGateway<br/>(wraps the rclpy Node: publishers, subscriptions,<br/>clients, timers, clock, tf, parameters;<br/>turns QosSpec into QoSProfile)"]
    end
  end

  Content --> NavPage
  Content --> PlanPage
  Content --> SimPage
  Content --> SystemPage
  NavPage --> RunCards
  NavPage --> ObsCards
  PlanPage --> PlanCards
  PlanCards --> ObsCards
  SimPage --> SimCards
  SystemPage --> SysCards

  RunCards --> RunVM
  RunCards --> GlobalVM
  RunCards --> TopoVM
  NavPage --> TelemetryVM
  NavPage --> AuthoringVM
  PlanCards --> PlanVM
  PlanCards --> MissionVM
  PlanVM -.->|"reads map"| TopoVM
  PlanVM -.->|"reads row action"| RunVM
  SimCards --> SimVM
  SimCards --> TopoVM
  SysCards --> TelemetryVM
  SysCards --> RobotBrainVM
  ObsCards --> ObstacleVM

  SimCards -.-> ProcReg
  SysCards -.-> ProcReg
  SysCards -.-> Medkit

  AuthoringVM --> TopologyEdit
  AuthoringVM --> TopologyApp
  AuthoringVM --> TelemetryApp
  AuthoringVM --> TopoVM
  AuthoringVM --> RunVM

  GlobalVM --> DriveApp
  RunVM --> DriveApp
  RunVM --> RowDiscApp
  RunVM --> NavApp
  TopoVM --> TopologyApp
  TelemetryVM --> TelemetryApp
  RobotBrainVM --> RobotBrainApp
  PlanVM --> PlanApp
  MissionVM --> MissionApp
  SimVM --> SimAssets
  SimVM --> SimWorld
  ObstacleVM -.-> ObstacleManager

  PlanApp --> TopologyEdit
  PlanApp --> TopologyApp
  PlanApp --> TelemetryApp
  PlanApp --> RowPlanning
  PlanApp --> F2CPkg
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

  class Content,NavPage,PlanPage,SimPage,SystemPage,RunCards,PlanCards,SimCards,SysCards,ObsCards view
  class Medkit todo
  class ProcReg proc
  class GlobalVM,RunVM,AuthoringVM,TopoVM,TelemetryVM,RobotBrainVM,PlanVM,MissionVM,ObstacleVM,SimVM vm
  class DriveApp,NavApp,RowDiscApp,RobotBrainApp,TelemetryApp,TopologyApp,TopologyEdit,PlanApp,MissionApp,SimAssets,SimWorld app
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

The Run, Plan, Sim and System tabs are page classes.
`NavPage` reaches `TopologyEditService` through `MapAuthoringViewModel`.
The Plan page reaches only ViewModels; the node passes it three callbacks (run, cancel and delete row), so the mission executor is not drawn as a dependency of the page.

## Decision: drop "Facade" as a layer name

Nothing built plays that role.
Its real job, a UI-neutral boundary that REST, MQTT and LoRa adapters can share, belongs to the application services.
The ViewModels can't take it on, because they are NiceGUI-specific.

### Names

<table>
<tr><th>Layer</th><th>Name</th><th>Rule</th></tr>
<tr><td>UI</td><td>Page</td><td>Layout only.
Imports ViewModels only (enforced by import-linter).
Tool processes are the one exception: cards start them through <code>process_registry</code>, and Medkit still uses <code>subprocess</code> in <code>tools_card</code></td></tr>
<tr><td>UI state</td><td>ViewModel</td><td>UI state and user intents.
It is the NiceGUI adapter, and the only place presentation formatting happens</td></tr>
<tr><td>Boundary</td><td>Application service</td><td>The "Robot API".
Use cases, plain dataclasses in and out, no UI or rclpy types.
Phase 2 adapters call this layer</td></tr>
<tr><td>Live state</td><td>Domain service</td><td>Holds live state (TopoDoc, mission state).
Talks to ROS only through RosGateway</td></tr>
<tr><td>Persistence</td><td>Store</td><td>TopoMapStore, MissionStore.
Owned by application services.
<code>TopoMapStore</code> sits in the <code>domain_services</code> package for now</td></tr>
<tr><td>Algorithms</td><td>Pure module</td><td>row_planning, topo_row_builder, plan_import.
No state, no ROS.
F2C is the separate <code>devkit_f2c_planner</code> package</td></tr>
<tr><td>ROS</td><td>RosGateway</td><td>Creates publishers, subscriptions, clients and timers, and reads node parameters.
The only module under the UI layers that imports <code>rclpy</code>.
Topic QoS is a <code>QosSpec</code> from <code>qos.py</code>, converted by the gateway</td></tr>
<tr><td>Processes</td><td>process_registry</td><td>Registry of long-running tool processes (RViz, Gazebo, explorer, grapher, neo), shared by every browser session.
No NiceGUI or ROS imports</td></tr>
<tr><td>Phase 2 edge</td><td>Adapter</td><td>RestAdapter, MqttAdapter, ReticulumAdapter.
Each is thin and calls application services.
Not built</td></tr>
<tr><td>Phase 2 central</td><td>Fleet Gateway</td><td>Renamed from "Central Gateway" so "gateway" means one thing only.
Not built</td></tr>
</table>

**Dependency rule:** Page → ViewModel → Application service → Domain service / Store / Pure module → RosGateway.
Nothing points upward or skips a layer.

import-linter enforces this in CI (`make lint-imports`, `.importlinter`) with seven contracts:

1. Pages import only ViewModels.
2. Pages do not spawn processes.
3. ViewModels do not reach domain services, stores or ROS.
4. Services are UI-neutral.
5. Domain services do not import application services.
6. Only RosGateway touches rclpy.
7. Field planning and plan import go through the Plan service: `ui_node`, the pages and the view models do not import `devkit_f2c_planner`, `row_planning` or `plan_import`.

Each exception is listed in `.importlinter` with a `TODO(#116)` and the check fails if one becomes stale.

### How the old Facade duties are split

- ROS message to plain type: `RosGateway` and the domain services.
- Plain type to display format: ViewModel.
- Plain type to wire format (JSON, protobuf, Reticulum frames): Adapter (Phase 2, not built).

### Consequences

- **The persistence objection no longer applies.**
  achurichi's rule was "no state or persistence in the Facade".
  That was about a layer that doesn't exist now.
  Stores sit in the application layer, as the build already has them.
- **Application services must stay thin orchestration.**
  Planning lives in pure modules and the executor in `MissionDomainService`.
  Otherwise the monolith concern comes straight back.
- **`MissionStore` no longer bypasses the layers.**
  In the 15 August diagram `MissionRunner` and `MissionStore` bypassed the Facade.
  `MissionViewModel` now goes through `MissionApplicationService`, which owns the store and the domain service.

## Proposed in #116 versus built

<table>
<tr><th>Proposed</th><th>Built</th></tr>
<tr><td>Facade layer (comment of 15 and 20 August: <code>NavFacade</code>, <code>MissionFacade</code>, <code>TopologyFacade</code>, <code>SystemFacade</code>)</td><td>Dropped.
<code>ApplicationService</code> is the boundary (comment of 9 October)</td></tr>
<tr><td>Three ViewModels: <code>NavViewModel</code>, <code>MissionViewModel</code>, <code>SystemViewModel</code>, plus <code>GlobalViewModel</code></td><td>Ten: <code>Global</code>, <code>Run</code>, <code>MapAuthoring</code>, <code>Topology</code>, <code>Telemetry</code>, <code>RobotBrain</code>, <code>Plan</code>, <code>Mission</code>, <code>Obstacle</code>, <code>Sim</code></td></tr>
<tr><td>Pages: Nav, Mission, System</td><td>Four tabs: Run, Plan, Sim, System.
All four are page classes: <code>NavPage</code>, <code>PlanPage</code>, <code>SimPage</code>, <code>SystemPage</code></td></tr>
<tr><td><code>NavigationService</code>, <code>MissionService</code>, <code>TopologyService</code>, <code>SystemService</code></td><td>Drive, Navigation, RowDiscovery, RobotBrain, Telemetry, Topology, TopologyEdit, Plan and Mission application services, each with a domain service, plus <code>SimAssetService</code> and <code>SimWorldService</code> (no ROS)</td></tr>
<tr><td><code>PlanningService (F2C)</code> / <code>PlanningDomainService</code></td><td><code>PlanApplicationService</code>, with no domain service of its own.
F2C is the <code>devkit_f2c_planner</code> package, called from <code>PlanApplicationService</code>.
Row geometry, row building and plan import are pure modules.
Edit rules are in <code>TopologyEditService</code></td></tr>
<tr><td><code>TopoRepo</code></td><td><code>TopoMapStore</code>, owned by <code>TopologyApplicationService</code></td></tr>
<tr><td><code>ROSGateway</code> as the ROS clients layer</td><td><code>RosGateway</code> wraps the rclpy node.
All seven domain services, <code>MissionStore</code> and <code>ObstacleManager</code> use it</td></tr>
<tr><td>"All UI contact points go through the Facade"</td><td>True for drive, navigation, row discovery, robot brain, telemetry, topology, planning and missions.
Not true for obstacles and process control (see below)</td></tr>
<tr><td>Enforce the dependency rule</td><td>Done: import-linter in CI</td></tr>
<tr><td>Phase 2: Edge API (REST, MQTT, VDA 5050), Fleet Gateway, standalone UI</td><td>Not started.
No adapters in the code</td></tr>
</table>

## Still open

Tracked in #116 and `.importlinter`:

- **Plan page state** is per browser session (`PlanPageState`, `MissionQueue`), but `PlanViewModel.save_status` is shared by every session.
  `PlanPage` takes `_run_mission`, `cancel_mission` and `confirm_delete_row` as callbacks from the node.
- **Plan page threading**: `PlanViewModel.plan` blocks, so the controls card calls it through `nicegui.run.io_bound`.
  Any other adapter has to run it on a worker thread too.
- **Mission start glue**: `_run_mission` and `_send_goal_sync` are still on the node, around `MissionDomainService.start`.
- **Node leftovers**: the `/current_node` subscription is still created in `NiceGuiNode`, through the gateway.
- **Obstacles**: `ObstacleViewModel` calls `ObstacleManager` directly.
  An `ObstacleApplicationService` is needed.
- **Medkit**: still started with `subprocess` in `pages/system/tools_card.py`.
- **Process control**: the other tool cards (Gazebo, row follow, RViz, explorer, grapher) start processes through `process_registry` from the page.
  A ViewModel and application service would complete the rule.
