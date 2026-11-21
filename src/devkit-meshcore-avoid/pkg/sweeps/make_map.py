#!/usr/bin/env python3
"""Write a synthetic field in the JSON map format that load_topo() reads.

Parallel rows with a node every --step metres (entry, waypoints, exit, named as the F2C planner does),
driven in alternating directions. A rail of short edges joins neighbouring row ends on both headlands,
as the planner's own maps do. There are no docks unless asked for: --docks N hangs N nodes named dock,
dock1, ... off the west headland, spread over the rows, and routes then end at them. Every node has a
stored id, so robots share one table.

  python sweeps/make_map.py -o field.json                      # 16 rows of 60 m, 1.2 m apart, a node every 3 m, no docks
  python sweeps/make_map.py --rows 30 --length 120 -o big.json
  python sweeps/make_map.py --docks 8 -o field_docks.json      # with 8 docks off the west headland
"""
import argparse
import json


def build(rows=16, length=60.0, spacing=1.2, step=3.0, docks=0):
    """Return the map as a dict in the fixture format. ValueError for a field that cannot be built."""
    k = round(length / step)
    if k < 2 or rows < 1 or not 0 <= docks <= rows:
        raise ValueError("need length of at least two steps, at least one row, and 0 to rows docks")
    nodes, edges, end = {}, [], {}

    def add(name, x, y, role, row):
        nodes[name] = dict(xy=[round(x, 3), round(y, 3)], role=role, row=row, id=len(nodes) + 1)

    for r in range(1, rows + 1):
        y, east = (r - 1) * spacing, r % 2 == 0           # even rows are driven east to west
        xs = [i * step for i in range(k + 1)][::-1 if east else 1]
        names = []
        for j, x in enumerate(xs):
            name, role = ((f"F2C_R{r}_IN", "entry") if j == 0 else (f"F2C_R{r}_OUT", "exit") if j == k
                          else (f"F2C_R{r}_W{j}", "waypoint"))
            add(name, x, y, role, r)
            names.append(name)
        edges += zip(names, names[1:])
        end[r, "W"], end[r, "E"] = (names[-1], names[0]) if east else (names[0], names[-1])
    for r in range(1, rows):
        edges += [(end[r, s], end[r + 1, s]) for s in "WE"]
    for i in range(docks):
        r = 1 + (round(i * (rows - 1) / (docks - 1)) if docks > 1 else 0)
        name = "dock" if i == 0 else f"dock{i}"
        add(name, -step, (r - 1) * spacing, "waypoint", 0)
        edges.append((name, end[r, "W"]))
    return dict(nodes=nodes, n_directed=2 * len(edges), n_two_way=2 * len(edges), edges=[list(e) for e in edges])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=16)
    ap.add_argument("--length", type=float, default=60.0, help="row length in metres, rounded to whole steps")
    ap.add_argument("--spacing", type=float, default=1.2, help="distance between rows in metres")
    ap.add_argument("--step", type=float, default=3.0, help="distance between nodes along a row in metres")
    ap.add_argument("--docks", type=int, default=0, help="docks off the west headland (default none)")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    with open(a.out, "w") as fh:
        json.dump(build(a.rows, a.length, a.spacing, a.step, a.docks), fh, indent=1)
        fh.write("\n")


if __name__ == "__main__":
    main()
