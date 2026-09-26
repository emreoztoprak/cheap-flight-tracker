"""Read and update .env files (KEY=VALUE lines), keeping comments and unrelated keys."""

from __future__ import annotations

import re
from collections.abc import Mapping

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_PLAIN = re.compile(r"^[A-Za-z0-9_@:./+,-]+$")


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        out, chars = [], iter(raw[1:-1])
        for char in chars:
            if char == "\\":
                nxt = next(chars, "")
                out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            else:
                out.append(char)
        return "".join(out)
    return raw


def _quote(value: str) -> str:
    if _PLAIN.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if match:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def update_env(text: str, changes: Mapping[str, str | None]) -> str:
    """Set (or, with None, remove) keys; other lines stay as they are, new keys go last."""
    pending = dict(changes)
    lines: list[str] = []
    for line in text.splitlines():
        match = _LINE.match(line) if not line.lstrip().startswith("#") else None
        if match and match.group(1) in pending:
            value = pending.pop(match.group(1))
            if value is not None:
                lines.append(f"{match.group(1)}={_quote(value)}")
            continue
        lines.append(line)
    for key, value in pending.items():
        if value is not None:
            lines.append(f"{key}={_quote(value)}")
    return "\n".join(lines) + "\n" if lines else ""
