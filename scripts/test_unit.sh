#!/usr/bin/env bash
set -euo pipefail
MARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARK_TEST_DIR="$(mktemp -d -t mark-tests-XXXXXXXX)"
trap 'rm -rf -- "$MARK_TEST_DIR"' EXIT
export MARK_KNOWLEDGE_DB="$MARK_TEST_DIR/knowledge.sqlite3"
"${MARK_PYTHON:-$HOME/.local/share/mark/core-env/bin/python}" -m unittest discover -s "$MARK_ROOT/tests" -p 'test_*.py' -v
