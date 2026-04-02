#!/bin/sh
# Replaces setuptools' .venv/bin/pocket after `make dev-setup` so the CLI works even when
# editable-install metadata in site-packages is missing or broken (common with mixed conda/venv).
# Installed path: <repo>/.venv/bin/pocket → repo root is two levels up from HERE.
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$HERE/python" -m pocket "$@"
