# Robot-to-robot avoidance simulator

An in-browser simulator for `sowbot_avoid`, the MeshCore collision-avoidance
protocol (M10). It runs the unmodified Python package in your browser using
Pyodide, so nothing is installed and no data leaves your machine.

```{raw} html
<p><a href="../avoid-sim/index.html"><strong>Open the simulator</strong></a></p>
```

Pick the number of robots, frame loss, clearance and protocol constants
(`ARBITRATE`, `TAIL`, `STUCK`, `SIDESTEP_MAX`), then press Run. The result is a
replay page with one cell per seed showing whether every robot finished and
whether the minimum separation fell below the clearance.

**Simulation only. This is not a safety function and must never be run on
hardware.** Results here are not evidence for the tracked platform.

The simulator is built from `src/devkit-meshcore-avoid/pkg/web/` by the docs
workflow on every push to `caatinga-dev`, so it always matches the code in
`pkg/sowbot_avoid/`. The Python API is documented on the
{doc}`Python API <../python_api>` page.
