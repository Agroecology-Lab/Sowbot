importScripts("https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js");
const FILES = ["sowbot_avoid/__init__.py", "sowbot_avoid/agent.py", "sowbot_avoid/driver.py", "sowbot_avoid/frame.py",
  "sowbot_avoid/graph.py", "sowbot_avoid/radio.py", "sowbot_avoid/sim.py", "sowbot_avoid/topo.py", "sowbot_avoid/vclock.py",
  "maize_sweep.py", "make_map.py", "export_replay.py", "replay_template.html", "vclock_web.py", "web_run.py"];
const ready = (async () => {
  const py = await loadPyodide();
  py.FS.mkdirTree("/app/sowbot_avoid");
  await Promise.all(FILES.map(async f => {
    const r = await fetch("py/" + f);
    if (!r.ok) throw new Error("missing py/" + f);
    py.FS.writeFile("/app/" + f, await r.text());
  }));
  py.runPython("import sys; sys.path.insert(0, '/app')");
  return py;
})();
onmessage = async e => {
  try {
    const py = await ready;
    postMessage({type: "ready"});
    if (!e.data) return;
    py.globals.set("P", py.toPy(e.data));
    py.globals.set("PROG", (i, n) => postMessage({type: "progress", i, n}));
    const html = py.runPython("import web_run\nweb_run.run(P.to_py(), PROG)");
    postMessage({type: "done", html});
  } catch (err) { postMessage({type: "error", msg: String(err)}); }
};
ready.then(() => postMessage({type: "ready"})).catch(err => postMessage({type: "error", msg: String(err)}));
