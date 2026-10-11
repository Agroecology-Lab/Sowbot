#!/usr/bin/env bash
# Source this file. rosys needs Python 3.11 to 3.13, so do not trust the default python3.

PYTHON=${PYTHON:-$(command -v python3.12 || command -v python3.13 || command -v python3.11)}
if [ -z "$PYTHON" ]; then
    echo "Python 3.11 to 3.13 not found. Install python3.12 or set PYTHON=/path/to/python." >&2
    return 1 2>/dev/null || exit 1
fi

if [ ! -d ".venv" ]; then
    "$PYTHON" -m venv .venv
fi

source .venv/bin/activate

pip install -r requirements-dev.txt
