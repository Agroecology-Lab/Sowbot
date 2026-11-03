#!/usr/bin/env python3
"""Sweep the sowbot_avoid agents over the real topological map (maps/maize_map by default).

Not part of the pytest run: the agents use wall-clock timing, so results depend on machine
load. Run it on an idle machine, one job at a time.

  python sweeps/maize_sweep.py --seeds 10
  python sweeps/maize_sweep.py --scenario opposed --robots 2 4 --loss 0 0.2 --park none --seeds 20
  python sweeps/maize_sweep.py --map tests/data/maize_map_fixture.json --seeds 3 --jsonl out.jsonl

Scenarios follow claimsim2: follow (same direction along the rows), opposed (alternating
directions), random (random start and goal) and local (start and goal within --local-radius
of an anchor). Every robot stands at its own start node from t=0.

--park docks   each route ends on its own dock node, hung 1.5 m off the goal (what the CLI demands today)
--park none    routes end on the map's own nodes, as in a field with no docks; a finished robot is
               ignored by its peers, so sep_parked shows what that costs
"""
import argparse
import asyncio
import heapq
import json
import math
import os
import random
import statistics
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sowbot_avoid.graph import Graph          # noqa: E402
from sowbot_avoid.sim import simulate         # noqa: E402
from sowbot_avoid.topo import load_topo       # noqa: E402

DOCK_OFFSET = 1.5
EPS = 1e-6      # NOTE: metres, floating-point noise when judging "closer than the clearance", not a safety margin


class Infeasible(Exception):
    """The map cannot hold this many robots at the required spacing."""


def default_map():
    d = HERE
    for _ in range(8):
        d = os.path.dirname(d)
        p = os.path.join(d, "maps", "maize_map")
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(HERE), "tests", "data", "maize_map_fixture.json")


# ---- routing -----------------------------------------------------------------------------------
def shortest(g, a, b):
    dist, prev, h = {a: 0.0}, {}, [(0.0, a)]
    while h:
        d, u = heapq.heappop(h)
        if u == b:
            break
        if d > dist[u]:
            continue
        for v in sorted(g.adj[u]):
            nd = d + math.dist(g.xy[u], g.xy[v])
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, u
                heapq.heappush(h, (nd, v))
    if b not in dist:
        return None
    out = [b]
    while out[-1] != a:
        out.append(prev[out[-1]])
    return out[::-1]


def rows_path(topo):
    """All rows in order, joined by shortest paths: the 'drive the whole field' route."""
    seq = [n for r in topo.rows for n in topo.row_nodes(r)]
    full = [seq[0]]
    for a, b in zip(seq, seq[1:]):
        if a != b:
            sp = shortest(topo.graph, a, b)
            if sp is None:
                raise Infeasible("rows are not connected")
            full += sp[1:]
    return full


def spaced(g, picks, cand, clearance):
    return all(math.dist(g.xy[cand], g.xy[p]) >= clearance for p in picks)


def scenario(topo, name, n, rng, local_radius):
    """Return n routes (lists of node ids) with distinct, mutually clear start nodes."""
    g, nodes, clr = topo.graph, sorted(topo.graph.xy), topo.graph.clearance
    routes, starts = [], []

    def add(route):
        if len(route) >= 3 and spaced(g, starts, route[0], clr):
            routes.append(route)
            starts.append(route[0])
            return True
        return False

    fwd = rows_path(topo) if name in ("follow", "opposed") else None
    for k in range(n):
        for _ in range(300):
            if name == "follow":
                i = rng.randrange(0, len(fwd) - 2)
                ok = add(fwd[i:])
            elif name == "opposed":
                i = rng.randrange(0, len(fwd))
                ok = add(fwd[i:] if k % 2 == 0 else fwd[:i + 1][::-1])
            else:
                pool = nodes
                if name == "local":
                    anchor = rng.choice(nodes)
                    pool = [m for m in nodes if math.dist(g.xy[anchor], g.xy[m]) <= local_radius]
                if len(pool) < 2:
                    continue
                a, b = rng.sample(pool, 2)
                sp = shortest(g, a, b)
                ok = bool(sp) and add(sp)
            if ok:
                break
        else:
            raise Infeasible(f"cannot place {n} robots {clr} m apart for {name}")
    rng.shuffle(routes)         # who gets the low id is random
    return routes


def add_docks(topo, routes):
    """Give each route its own dock node off its last node; return (graph, routes)."""
    g = topo.graph
    edges = {(a, b) for a in g.adj for b in g.adj[a] if a < b}
    xy = dict(g.xy)
    cx = statistics.fmean(p[0] for p in xy.values())
    cy = statistics.fmean(p[1] for p in xy.values())
    by_goal = defaultdict(list)
    for k, r in enumerate(routes):
        by_goal[r[-1]].append(k)
    nxt, out, docks = max(xy) + 1, [list(r) for r in routes], set()
    for goal, ks in by_goal.items():
        base = math.atan2(xy[goal][1] - cy, xy[goal][0] - cx)
        for j, k in enumerate(ks):
            ang = base + (j - (len(ks) - 1) / 2) * math.radians(60)
            xy[nxt] = (xy[goal][0] + DOCK_OFFSET * math.cos(ang), xy[goal][1] + DOCK_OFFSET * math.sin(ang))
            edges.add((goal, nxt))
            out[k].append(nxt)
            docks.add(nxt)
            nxt += 1
    return Graph(sorted(edges), xy, g.clearance, docks), out


# ---- one run -----------------------------------------------------------------------------------
def run_one(topo, sc, n, loss, seed, park, a):
    """Run one scenario and return simulation results with sweep metadata.

    Park mode "docks" adds a dock to each route. Options in a supply local
    radius in meters, start stagger and timeout in unscaled seconds, and scale.
    The result includes wall-clock time in seconds and the total route hops.
    Infeasible propagates if routes cannot be constructed. Simulation timeouts
    return partial results; other simulation and event-loop errors propagate.
    """
    rng = random.Random(f"{sc}/{n}/{seed}")             # same routes for every loss and park setting
    routes = scenario(topo, sc, n, rng, a.local_radius)
    g = topo.graph
    if park == "docks":
        g, routes = add_docks(topo, routes)
    delays = {i: rng.uniform(0, a.stagger) for i in range(1, n + 1)} if a.stagger else None
    t0 = time.monotonic()
    r = asyncio.run(simulate({i: rt for i, rt in enumerate(routes, 1)}, loss, seed, a.scale, a.timeout,
                             graph=g, delays=delays))
    r.update(scenario=sc, n=n, loss=loss, seed=seed, park=park,
             wall=time.monotonic() - t0, hops=sum(len(x) - 1 for x in routes))
    return r


# ---- report ------------------------------------------------------------------------------------
def fmt(x):
    return "inf" if x == math.inf else f"{x:.2f}"


def verdict(g, clr):
    """Return (status, detail) for one group of runs: PASS, WARN or FAIL.

    FAIL: a run did not finish, or two running robots came closer than the clearance.
    WARN: everything finished and running robots stayed clear, but a robot passed a
    finished one closer than the clearance (peers cannot see a finished robot).
    Otherwise the status is PASS. clr is in meters; a separation counts as too
    close only when it is strictly below clr minus 1e-6 meters. An empty group
    raises ValueError.
    """
    n = len(g)
    unfinished = sum(not r["done"] for r in g)
    live_bad = sum(r["sep_live"] < clr - EPS for r in g)
    park_bad = sum(r["sep_parked"] < clr - EPS for r in g)
    closest = fmt(min(r["sep_live"] for r in g))
    if unfinished or live_bad:
        why = []
        if unfinished:
            why.append(f"{unfinished} of {n} runs did not finish")
        if live_bad:
            why.append(f"{live_bad} of {n} runs had two moving robots under {clr:g} m (closest {closest} m)")
        return "FAIL", "; ".join(why)
    if park_bad:
        closest_park = fmt(min(r["sep_parked"] for r in g))
        return "WARN", (f"{n}/{n} runs finished, but {park_bad} passed a finished robot "
                        f"under {clr:g} m (closest {closest_park} m)")
    return "PASS", f"{n}/{n} runs finished, closest moving pair {closest} m"


def summary(groups, clr):
    """Print one verdict per case, totals and a legend, using clearance clr in meters.

    Raise ValueError if groups or any group of runs is empty.
    """
    print("\nSummary")
    counts = defaultdict(int)
    for k in sorted(groups):
        status, detail = verdict(groups[k], clr)
        counts[status] += 1
        print(f"  {status:4s}  {k[0]}, {k[1]} robots, loss {k[2]:.2f}, {k[3]}: {detail}")
    print(f"  Overall: {counts['PASS']} PASS, {counts['WARN']} WARN, {counts['FAIL']} FAIL of {len(groups)} cases.")
    print("  PASS: all finished and kept clear. WARN: also passed a finished robot too close. FAIL: a run did not finish, or moving robots came too close.")
    if min(len(g) for g in groups.values()) < 5:
        print("  Fewer than 5 seeds in some cases: one pass proves little. Use --seeds 5 or more.")


def report(rows, clr):
    """Print the verdict summary and detailed table for runs, using clearance clr in meters.

    Raise ValueError if rows is empty.
    """
    groups = defaultdict(list)
    for r in rows:
        groups[(r["scenario"], r["n"], r["loss"], r["park"])].append(r)
    summary(groups, clr)
    print("\nDetails")
    print(f"\n{'scenario':8s} {'n':>2s} {'loss':>4s} {'park':5s} | {'runs':>4s} {'done%':>5s} "
          f"{'live<clr':>8s} {'min live':>8s} {'parked<clr':>10s} {'min park':>8s} | {'secs':>5s} {'frames':>6s} {'side':>4s}")
    for k in sorted(groups):
        g = groups[k]
        live = [r["sep_live"] for r in g]
        park = [r["sep_parked"] for r in g]
        print(f"{k[0]:8s} {k[1]:2d} {k[2]:4.2f} {k[3]:5s} | {len(g):4d} "
              f"{100 * sum(r['done'] for r in g) / len(g):5.0f} {sum(x < clr - EPS for x in live):8d} {fmt(min(live)):>8s} "
              f"{sum(x < clr - EPS for x in park):10d} {fmt(min(park)):>8s} | "
              f"{statistics.median(r['secs'] for r in g):5.0f} {statistics.median(r['frames'] for r in g):6.0f} "
              f"{statistics.fmean(r['sidesteps'] for r in g):4.1f}")
    print("\nlive<clr: runs where two running robots came closer than the clearance. "
          "parked<clr: runs where a robot passed a finished one that close (peers cannot see it). "
          "secs: median unscaled seconds.")


def main():
    """Run the requested sweep, print results and optionally write JSONL.

    Infeasible scenario and robot-count combinations are skipped.
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", default=default_map(), help="topological map YAML, or a JSON fixture")
    ap.add_argument("--scenario", nargs="+", default=["follow", "opposed", "random", "local"],
                    choices=["follow", "opposed", "random", "local"])
    ap.add_argument("--robots", nargs="+", type=int, default=[2, 4, 6])
    ap.add_argument("--loss", nargs="+", type=float, default=[0.0, 0.1, 0.2])
    ap.add_argument("--park", choices=["docks", "none", "both"], default="docks")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--scale", type=float, default=0.1, help="timer scale; 0.1 runs ten times faster than real time")
    ap.add_argument("--timeout", type=float, default=600, help="unscaled seconds before a run counts as not finished")
    ap.add_argument("--clearance", type=float, default=1.0)
    ap.add_argument("--local-radius", type=float, default=8.0)
    ap.add_argument("--stagger", type=float, default=0.0, help="agents start up to this many unscaled seconds late")
    ap.add_argument("--jsonl", help="write one JSON line per run")
    a = ap.parse_args()
    if a.clearance <= EPS:
        ap.error(f"--clearance must be greater than {EPS:g} m")

    topo = load_topo(a.map, a.clearance)
    xs, ys = [p[0] for p in topo.graph.xy.values()], [p[1] for p in topo.graph.xy.values()]
    print(f"map {a.map}\n  {len(topo.ids)} nodes, {len(topo.graph.xy)} with coordinates, {len(topo.rows)} rows, "
          f"{max(xs) - min(xs):.1f} x {max(ys) - min(ys):.1f} m, {topo.n_directed} directed edges "
          f"({topo.n_two_way} two-way), {len(topo.zero_length)} zero-length, ids {topo.digest} "
          f"({'stored in map' if topo.ids_stored else 'assigned by sorted name'})")
    parks = ["docks", "none"] if a.park == "both" else [a.park]
    out = open(a.jsonl, "w") if a.jsonl else None
    rows, skipped = [], set()
    jobs = [(sc, n, lo, s, pk) for sc in a.scenario for n in a.robots for lo in a.loss
            for pk in parks for s in range(a.seed0, a.seed0 + a.seeds)]
    for i, (sc, n, lo, s, pk) in enumerate(jobs, 1):
        if (sc, n) in skipped:
            continue
        try:
            r = run_one(topo, sc, n, lo, s, pk, a)
        except Infeasible as e:
            skipped.add((sc, n))
            print(f"skip {sc} n={n}: {e}", file=sys.stderr)
            continue
        rows.append(r)
        if out:
            out.write(json.dumps({k: (None if v == math.inf else v) for k, v in r.items()}) + "\n")
            out.flush()
        print(f"\r{i}/{len(jobs)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    if rows:
        report(rows, a.clearance)


if __name__ == "__main__":
    main()
