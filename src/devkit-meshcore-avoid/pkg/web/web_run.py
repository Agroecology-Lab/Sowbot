"""Entry point for the browser: run one configuration over several seeds and return the replay page."""
import asyncio
import json
import types

from sowbot_avoid import agent, vclock
import vclock_web

vclock.VirtualLoop = vclock_web.WebVirtualLoop
_run = vclock.run


def _run_detached(coro):
    """Pyodide keeps its own loop marked as running; hide it while the virtual loop runs."""
    prev = asyncio.events._get_running_loop()
    asyncio.events._set_running_loop(None)
    try:
        return _run(coro)
    finally:
        asyncio.events._set_running_loop(prev)


vclock.run = _run_detached

import export_replay as er  # noqa: E402
import maize_sweep as ms  # noqa: E402
from make_map import build  # noqa: E402
from sowbot_avoid.topo import load_topo  # noqa: E402


def run(p, progress=lambda i, n: None):
    for k in ("ARBITRATE", "TAIL", "STUCK", "SIDESTEP_MAX", "LOOKAHEAD"):
        setattr(agent, k, p[k])
    with open("/tmp/map.json", "w") as fh:
        json.dump(build(p["rows"], p["length"]), fh)
    a = types.SimpleNamespace(scenario="random", robots=[p["n"]], loss=[p["loss"]], seeds=p["seeds"],
                              clearance=p["clearance"], react=p["react"], legs=p["legs"], docks=False,
                              timeout=p["timeout"], local_radius=8.0, stagger=0.0, scale=1.0, virtual=True)
    er._init("/tmp/map.json", a.clearance)
    cells, logs, k = [], {}, f"{p['n']}|{p['loss']:g}"
    for seed in range(a.seeds):
        n, loss, s, r, log = er._job((p["n"], p["loss"], seed, a))
        if r is None:
            raise ValueError("infeasible: too many robots for this map")
        live = r["sep_live"]
        cells.append([seed, bool(r["done"]), live < a.clearance - ms.EPS, None if live == float("inf") else round(live, 2),
                      round(r["secs"]), r["frames"], r["lost"], r["sidesteps"]])
        logs[f"{k}|{seed}"] = log
        progress(seed + 1, a.seeds)
    g = load_topo("/tmp/map.json", a.clearance, drop_docks=True).graph
    data = dict(meta=dict(map="generated", scenario="random", legs=a.legs, react=a.react, clr=a.clearance,
                          seeds=a.seeds, lookahead=p["LOOKAHEAD"], commit="web"),
                n=[p["n"]], loss=[p["loss"]], cells={k: cells}, logs=logs,
                xy={str(i): list(q) for i, q in g.xy.items()},
                edges=sorted([x, y] for x in g.adj for y in g.adj[x] if x < y))
    with open("/app/replay_template.html") as fh:
        return fh.read().replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
