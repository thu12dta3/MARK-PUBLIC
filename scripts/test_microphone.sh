#!/usr/bin/env bash
set -euo pipefail
MARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$MARK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "${MARK_PYTHON:-$HOME/.local/share/mark/core-env/bin/python}" - "$MARK_ROOT" <<'PY'
import sys
from mark.audio import ContinuousMicrophone
from mark.config import load_config
mic=ContinuousMicrophone(load_config())
try:
    mic.start()
    print('Microfone ativo; capturando 2 segundos...')
    for _ in range(67):
        if not mic.read_frame(timeout=1.0):
            raise RuntimeError('sem dados do microfone após 1 segundo; confira o dispositivo e a fonte PulseAudio padrão')
    print('Captura contínua OK.')
finally: mic.stop()
PY
