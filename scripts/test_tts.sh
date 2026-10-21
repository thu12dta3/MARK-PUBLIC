#!/usr/bin/env bash
set -euo pipefail
MARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$MARK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "${MARK_PYTHON:-$HOME/.local/share/mark/core-env/bin/python}" - <<'PY'
from mark.config import load_config
from mark.tts import speak
speak('Teste de voz do MARK. Sistema de áudio funcionando.', 'pt', load_config())
PY
