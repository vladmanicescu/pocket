"""Allow ``python -m pocket`` (with ``PYTHONPATH=src`` or after editable install)."""

from __future__ import annotations

from pocket.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
