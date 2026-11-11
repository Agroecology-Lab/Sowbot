"""Fail if a package under src/ has no page in the built docs.

Run after sphinx-build, with the build log as the first argument. autosummary
only warns when it cannot import a module, and the build uses --keep-going, so
without this check a broken import would drop a module from the published docs
without anyone noticing.
"""
import re
import sys
from pathlib import Path

from _packages import discover

docs = Path(__file__).resolve().parent
html = docs / "_build" / "html"
importable, file_only = discover()
rst = (docs / "python_api.rst").read_text(encoding="utf-8")

problems = []
for module in importable:
    if not re.search(rf"^\s+{re.escape(module)}\s*$", rst, re.MULTILINE):
        problems.append(f"{module}: not listed in docs/python_api.rst")
    elif not (html / "_autosummary" / f"{module}.html").exists():
        problems.append(f"{module}: listed, but no page was built (import failed?)")
if file_only and not (html / "_generated" / "launch_files.html").exists():
    problems.append("launch_files page was not built")

if len(sys.argv) > 1:
    log = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    for line in log.splitlines():
        if "failed to import" in line:
            problems.append(line.strip())

if problems:
    print("Docs coverage check failed:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print(f"Docs coverage OK: {len(importable)} importable packages ({', '.join(importable)}), "
      f"{len(file_only)} file-only ({', '.join(file_only) or 'none'}).")
