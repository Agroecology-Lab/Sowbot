#!/bin/sh
# Assemble the static site into ${1:-_site}. Run from pkg/.
set -e
o=${1:-_site}; rm -rf "$o"; mkdir -p "$o/py/sowbot_avoid"
cp sowbot_avoid/*.py "$o/py/sowbot_avoid/"
cp sweeps/maize_sweep.py sweeps/make_map.py sweeps/export_replay.py sweeps/replay_template.html web/vclock_web.py web/web_run.py "$o/py/"
cp web/index.html web/worker.js "$o/"
