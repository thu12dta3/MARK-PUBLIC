#!/usr/bin/env bash
set -euo pipefail
MARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$MARK_ROOT"
export PYTHONPATH="$MARK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="${MARK_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in "$MARK_ROOT/venv/bin/python" "$MARK_ROOT/.venv/bin/python" "$HOME/.local/share/mark/core-env/bin/python" python3 /usr/bin/python3; do
    if [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
exec "$PYTHON" -m mark.ui "$@"
