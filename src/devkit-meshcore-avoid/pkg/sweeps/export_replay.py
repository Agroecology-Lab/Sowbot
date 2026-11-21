#!/usr/bin/env python3
"""Run a sweep on the virtual clock and write one self-contained HTML page: a heat-map of the results
and a replay of any run, so a failing seed can be watched.

The unmodified agents run. Subclasses only record what the driver, the channel and the agent's own
log calls already do. Needs pyyaml for the real YAML map.

  python sweeps/export_replay.py --map ../../../maps/maize_map -o sweep.html
  python sweeps/export_replay.py --robots 2 3 --loss 0 0.1 --seeds 50 -o sweep.html

The page has no server and no dependencies: put the file on GitHub Pages.
"""
import argparse
import contextlib
import json
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import maize_sweep as ms                 # noqa: E402
from sowbot_avoid import sim             # noqa: E402
from sowbot_avoid.topo import load_topo  # noqa: E402

LOG = {}


def _now():
    return round(time.monotonic(), 2)


class LogBus(sim.LoopBus):
    def _deliver(self, sender, payload, me):
        before = self.lost
        super()._deliver(sender, payload, me)
        LOG["tx"].append([_now(), payload[0], int(self.lost > before)])


class LogDriver(sim.SimDriver):
    async def drive(self, a, b):
        t0 = _now()
        await super().drive(a, b)
        LOG["drive"].append([self.rid, t0, _now(), a, b])


class LogAgent(sim.Agent):
    def __init__(self, rid, route, *a, **k):
        LOG["routes"][rid] = list(route)
        super().__init__(rid, route, *a, **k)

    @property
    def log(self):
        return self._log

    @log.setter
    def log(self, fn):      # simulate() replaces .log after construction: wrap whatever it sets
        def wrapped(*args):
            if args and args[0] == "finished":
                LOG["fin"][self.rid] = _now()
            elif args and str(args[0]).startswith("side-step"):
                LOG["ss"].append([_now(), self.rid])
            fn(*args)
        self._log = wrapped


@contextlib.contextmanager
def recording():
    """Swap the recording classes into sim for the duration, so importing this module changes nothing."""
    old = sim.LoopBus, sim.SimDriver, sim.Agent
    sim.LoopBus, sim.SimDriver, sim.Agent = LogBus, LogDriver, LogAgent
    try:
        yield
    finally:
        sim.LoopBus, sim.SimDriver, sim.Agent = old


def commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", default=ms.default_map())
    ap.add_argument("--scenario", default="random", choices=["follow", "opposed", "random", "local"])
    ap.add_argument("--robots", nargs="+", type=int, default=[2, 3, 4])
    ap.add_argument("--loss", nargs="+", type=float, default=[0.0, 0.1, 0.2])
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--clearance", type=float, default=1.0)
    ap.add_argument("-o", "--out", default="meshcore_avoid_sweep.html")
    a = ap.parse_args(argv)
    a.local_radius, a.stagger, a.scale, a.timeout, a.virtual = 8.0, 0.0, 1.0, 600, True

    topo = load_topo(a.map, a.clearance)
    g = topo.graph
    cells, logs, done_n = {}, {}, []
    with recording():
        for n in a.robots:
            for loss in a.loss:
                for seed in range(a.seeds):
                    LOG.clear()
                    LOG.update(routes={}, tx=[], drive=[], fin={}, ss=[])
                    try:
                        r = ms.run_one(topo, a.scenario, n, loss, seed, a)
                    except ms.Infeasible as e:
                        print(f"skip {n} robots: {e}", file=sys.stderr)
                        break
                    k = f"{n}|{loss:g}"
                    live = r["sep_live"]
                    cells.setdefault(k, []).append([
                        seed, bool(r["done"]), live < a.clearance - ms.EPS, None if live == math.inf else round(live, 2),
                        round(r["secs"]), r["frames"], r["lost"], r["sidesteps"]])
                    logs[f"{k}|{seed}"] = json.loads(json.dumps(LOG))
                else:
                    continue
                break
            else:
                done_n.append(n)
    if not cells:
        sys.exit("no feasible runs")
    data = dict(
        meta=dict(map=os.path.basename(a.map), scenario=a.scenario, clr=a.clearance, seeds=a.seeds, commit=commit()),
        n=done_n, loss=a.loss, cells=cells, logs=logs,
        xy={str(i): list(p) for i, p in g.xy.items()},
        edges=sorted([x, y] for x in g.adj for y in g.adj[x] if x < y))
    with open(os.path.join(HERE, "replay_template.html")) as fh:
        page = fh.read().replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    with open(a.out, "w") as fh:
        fh.write(page)
    print(f"{a.out}: {len(cells)} cells, {len(logs)} runs, {len(page) // 1024} KB", file=sys.stderr)


if __name__ == "__main__":
    main()
