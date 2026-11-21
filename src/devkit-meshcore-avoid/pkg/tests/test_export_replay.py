import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sweeps"))
import export_replay as ex  # noqa: E402
from sowbot_avoid import sim  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "data", "maize_map_fixture.json")


def test_export_writes_a_page_with_cells_and_event_logs(tmp_path):
    out = tmp_path / "p.html"
    originals = sim.LoopBus, sim.SimDriver, sim.Agent
    ex.main(["--map", FIXTURE, "--robots", "2", "--loss", "0", "--seeds", "2", "-o", str(out)])
    assert (sim.LoopBus, sim.SimDriver, sim.Agent) == originals      # recording classes are swapped back out
    html = out.read_text()
    assert "/*DATA*/" not in html
    d = json.loads(html.split("const D=", 1)[1].split(";\n", 1)[0])
    assert d["n"] == [2] and len(d["cells"]["2|0"]) == 2 and set(d["logs"]) == {"2|0|0", "2|0|1"}
    log = d["logs"]["2|0|0"]
    assert set(log["routes"]) == {"1", "2"} and log["drive"] and log["tx"]
