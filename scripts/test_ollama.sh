#!/usr/bin/env bash
set -euo pipefail
MARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$MARK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "${MARK_PYTHON:-$HOME/.local/share/mark/core-env/bin/python}" - <<'PY'
import sys
from mark.brain import _ollama
from mark.config import load_config
from mark.selfcheck import ollama_status
cfg = load_config()
ready, message = ollama_status(cfg)
if not ready:
    print(f'FALTA: {message}', file=sys.stderr)
    raise SystemExit(1)
print(_ollama('Responda apenas: MARK online.', cfg))
PY
