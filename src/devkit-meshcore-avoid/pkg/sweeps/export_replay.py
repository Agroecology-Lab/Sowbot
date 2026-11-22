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
import multiprocessing
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import maize_sweep as ms                 # noqa: E402
from sowbot_avoid import sim             # noqa: E402
from sowbot_avoid.frame import Frame     # noqa: E402
from sowbot_avoid.topo import load_topo  # noqa: E402

LOG = {}
TOPO = None


def _now():
    return round(time.monotonic(), 2)


class LogBus(sim.LoopBus):
    def _deliver(self, sender, payload, me):
        before = self.lost
        super()._deliver(sender, payload, me)
        f = Frame.decode(payload)
        LOG["tx"].append([_now(), payload[0], int(self.lost > before), list(f.nodes) if f else []])


class LogDriver(sim.SimDriver):
    def _frac(self, a, b):
        """How far along edge a -> b the robot is, 0 to 1."""
        (ax, ay), (bx, by) = self.g.xy[a], self.g.xy[b]
        full = math.hypot(bx - ax, by - ay)
        return 1.0 if full == 0 else min(1.0, math.hypot(self.at[0] - ax, self.at[1] - ay) / full)

    async def drive(self, a, b):
        t0 = _now()
        try:
            await super().drive(a, b)
        finally:       # also when the agent cancels it part-way: the segment ends at the fraction reached
            LOG["drive"].append([self.rid, t0, _now(), a, b, 0, round(self._frac(a, b), 4)])

    async def retreat(self, a, b):
        t0, f = _now(), self._frac(a, b)
        await super().retreat(a, b)
        LOG["drive"].append([self.rid, t0, _now(), a, b, round(f, 4), 0])


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
            elif args and args[0] == "stopped mid-edge":
                LOG["stop"].append([_now(), self.rid])
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


def _init(path, clearance, docks=False):
    global TOPO
    TOPO = load_topo(path, clearance, drop_docks=not docks)


def _job(job):
    """One run, in a worker. Returns (n, loss, seed, result or None if infeasible, event log)."""
    n, loss, seed, a = job
    with recording():
        LOG.clear()
        LOG.update(routes={}, tx=[], drive=[], fin={}, ss=[], stop=[])
        try:
            r = ms.run_one(TOPO, a.scenario, n, loss, seed, a)
        except ms.Infeasible as e:
            print(f"skip {n} robots: {e}", file=sys.stderr)
            return n, loss, seed, None, None
        return n, loss, seed, r, json.loads(json.dumps(LOG))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", default=ms.default_map())
    ap.add_argument("--scenario", default="random", choices=["follow", "opposed", "random", "local"])
    ap.add_argument("--robots", nargs="+", type=int, default=[2, 3, 4])
    ap.add_argument("--loss", nargs="+", type=float, default=[0.0, 0.1, 0.2])
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--clearance", type=float, default=1.0)
    ap.add_argument("--react", action="store_true", help="robots stop part-way along an edge when a frame shows a conflict")
    ap.add_argument("--legs", type=int, default=1, help="legs per route: 6 makes runs about six times as long")
    ap.add_argument("--docks", action="store_true", help="keep the map's docks (default: removed)")
    ap.add_argument("--timeout", type=float, default=None, help="simulated seconds before a run counts as not finished (default 900 per leg)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="worker processes")
    ap.add_argument("-o", "--out", default="meshcore_avoid_sweep.html")
    a = ap.parse_args(argv)
    a.local_radius, a.stagger, a.scale, a.virtual = 8.0, 0.0, 1.0, True
    if a.legs < 1:
        ap.error("--legs must be at least 1")
    if a.timeout is None:
        a.timeout = 900.0 * a.legs

    jobs = [(n, loss, seed, a) for n in a.robots for loss in a.loss for seed in range(a.seeds)]
    if a.jobs > 1:
        results = multiprocessing.Pool(a.jobs, _init, (a.map, a.clearance, a.docks)).imap_unordered(_job, jobs)
    else:
        _init(a.map, a.clearance, a.docks)
        results = map(_job, jobs)
    cells, logs, skipped = {}, {}, set()
    for i, (n, loss, seed, r, log) in enumerate(results, 1):
        print(f"\r{i}/{len(jobs)}", end="", file=sys.stderr, flush=True)
        if r is None:
            skipped.add(n)
            continue
        k = f"{n}|{loss:g}"
        live = r["sep_live"]
        cells.setdefault(k, []).append([
            seed, bool(r["done"]), live < a.clearance - ms.EPS, None if live == math.inf else round(live, 2),
            round(r["secs"]), r["frames"], r["lost"], r["sidesteps"]])
        logs[f"{k}|{seed}"] = log
    print(file=sys.stderr)
    cells = {k: sorted(v) for k, v in cells.items() if int(k.split("|")[0]) not in skipped}
    logs = {k: v for k, v in logs.items() if int(k.split("|")[0]) not in skipped}
    done_n = [n for n in a.robots if n not in skipped]
    g = load_topo(a.map, a.clearance, drop_docks=not a.docks).graph
    if not cells:
        sys.exit("no feasible runs")
    data = dict(
        meta=dict(map=os.path.basename(a.map), scenario=a.scenario, legs=a.legs, react=a.react, clr=a.clearance, seeds=a.seeds, commit=commit()),
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
