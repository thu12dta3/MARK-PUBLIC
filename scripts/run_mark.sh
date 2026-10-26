#!/usr/bin/env bash
set -euo pipefail
MARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${MARK_PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for candidate in "$MARK_ROOT/venv/bin/python" "$MARK_ROOT/.venv/bin/python" "$HOME/.local/share/mark/core-env/bin/python" python3 /usr/bin/python3; do
        if [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
fi
export PYTHONPATH="$MARK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ " $* " == *" --serve-mobile "* ]] && command -v tailscale >/dev/null 2>&1; then
    tailscale serve --http=80 off >/dev/null 2>&1 || true
    tailscale serve --bg --https=443 http://127.0.0.1:8765 >/dev/null 2>&1 || true
fi
cd "$MARK_ROOT"
exec "$PYTHON" -m mark.mark "$@"
