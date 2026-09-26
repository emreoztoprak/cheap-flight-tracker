"""Log to stdout (and optionally a rotating file) as text or JSON, with secrets masked."""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            data["exception"] = record.exc_text
        return json.dumps(data, ensure_ascii=False)


class RedactingFormatter(logging.Formatter):
    def __init__(self, inner: logging.Formatter, secrets: Iterable[str]) -> None:
        super().__init__()
        self._inner = inner
        self._secrets = [secret for secret in secrets if secret and len(secret) >= 4]

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def format(self, record: logging.LogRecord) -> str:
        # Redact before formatting too: JSON escaping would hide secrets containing " or \.
        clean = logging.makeLogRecord(record.__dict__)
        clean.msg, clean.args = self._redact(record.getMessage()), None
        if record.exc_info:
            clean.exc_text = self._redact(self._inner.formatException(record.exc_info))
            clean.exc_info = None
        return self._redact(self._inner.format(clean))


@dataclass(frozen=True)
class LogEntry:
    created: float
    level: str
    text: str


class LogBuffer:
    """The most recent formatted log lines, for the web UI."""

    def __init__(self, capacity: int = 500) -> None:
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        buffer = self

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    entry = LogEntry(record.created, record.levelname, self.format(record))
                except Exception:
                    self.handleError(record)
                    return
                with buffer._lock:
                    buffer._entries.append(entry)

        self.handler: logging.Handler = _Handler()

    def entries(self, min_level: str = "DEBUG", limit: int | None = None) -> list[LogEntry]:
        threshold = logging.getLevelName(min_level.upper())
        with self._lock:
            picked = [e for e in self._entries if logging.getLevelName(e.level) >= threshold]
        return picked[-limit:] if limit else picked


def configure_logging(
    level: str,
    fmt: str,
    secrets: Iterable[str] = (),
    stream: IO[str] | None = None,
    *,
    file: Path | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backups: int = 5,
    buffer: LogBuffer | None = None,
) -> None:
    inner = JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT)
    formatter = RedactingFormatter(inner, list(secrets))
    handlers: list[logging.Handler] = [logging.StreamHandler(stream or sys.stdout)]
    file_problem = None
    if file is not None:
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
            )
        except OSError as exc:
            file_problem = exc
    if buffer is not None:
        handlers.append(buffer.handler)
    root = logging.getLogger()
    for old in root.handlers:
        if getattr(old, "_cheap_flights", False):
            old.close()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler._cheap_flights = True  # type: ignore[attr-defined]
    root.handlers[:] = handlers
    # The chosen level applies to this tool's own loggers only. Libraries (httpx, and primp's
    # Rust internals: h2, hickory, cookie_store...) stay at WARNING, even with --log-level DEBUG;
    # at INFO they would also log request URLs, and the Telegram URL contains the bot token.
    root.setLevel(logging.WARNING)
    logging.getLogger("cheap_flights").setLevel(level.upper())
    if file_problem is not None:
        logging.getLogger("cheap_flights.logging").warning(
            "cannot write log file %s (%s); logging to stdout only", file, file_problem
        )
