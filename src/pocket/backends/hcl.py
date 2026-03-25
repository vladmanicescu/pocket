"""
Minimal HCL serialiser for terraform.tfvars generation.

Only covers the subset of HCL types that appear in this project's tfvars files:
  - strings          → "value"
  - booleans         → true / false
  - integers         → 2
  - string lists     → ["a", "b"]
  - object lists     → [{ key = "val"  other = 3 }, ...]

All public functions return a plain str ready to embed in a tfvars file.
"""

from __future__ import annotations


def string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def boolean(value: bool) -> str:
    return "true" if value else "false"


def number(value: int | float) -> str:
    return str(value)


def string_list(values: list[str]) -> str:
    items = ", ".join(string(v) for v in values)
    return f"[{items}]"


def object_list(rows: list[dict], indent: int = 2) -> str:
    """Render a list of flat dicts as an HCL object list.

    Values in each dict may be str, bool, int, or float; None values are
    skipped so optional fields are omitted cleanly.

    Example output:
        [
          {
            name            = "k8s-cp1"
            hostname        = "k8s-cp1"
            private_ip      = "172.31.1.11"
            extra_disk_size = 3
          },
        ]
    """
    if not rows:
        return "[]"

    pad = " " * indent
    # Align '=' signs within each object to the longest key
    max_key = max(len(k) for row in rows for k in row if row[k] is not None)

    parts: list[str] = ["["]
    for i, row in enumerate(rows):
        parts.append(f"{pad}{{")
        for key, val in row.items():
            if val is None:
                continue
            rendered = _render_scalar(val)
            parts.append(f"{pad}  {key:<{max_key}} = {rendered}")
        suffix = "," if i < len(rows) - 1 else ""
        parts.append(f"{pad}}}{suffix}")
    parts.append("]")
    return "\n".join(parts)


def assignment(name: str, value: str) -> str:
    """Return a single 'name = value' line (value already rendered)."""
    return f"{name} = {value}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _render_scalar(value: bool | int | float | str) -> str:
    if isinstance(value, bool):
        return boolean(value)
    if isinstance(value, (int, float)):
        return number(value)
    return string(str(value))
