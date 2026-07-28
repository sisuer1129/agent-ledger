#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHONPATH=release/app "$PYTHON_BIN" -c 'from seed_catalog import load_category_seed, load_tag_seed; load_category_seed(); load_tag_seed()'
"$PYTHON_BIN" -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/frontend_contract.test.mjs
PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/agent-ledger-pyc}" "$PYTHON_BIN" -m py_compile release/app/*.py
if rg -n -U 'catch\s*\([^)]*\)\s*\{\s*\}' release/frontend; then exit 1; fi
