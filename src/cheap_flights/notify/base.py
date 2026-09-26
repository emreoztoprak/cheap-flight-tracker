"""Channel-neutral message model and fan-out to every configured channel."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import IO, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Line:
    text: str
    url: str | None = None


@dataclass(frozen=True)
class Message:
    title: str
    lines: tuple[Line, ...] = ()
    footer: str = ""

    def plain(self) -> str:
        out = [self.title, ""]
        for line in self.lines:
            out.append(line.text)
            if line.url:
                out.append(f"   {line.url}")
        if self.footer:
            out += ["", self.footer]
        return "\n".join(out)


class NotifyError(Exception):
    """A channel could not deliver a message."""


class Notifier(Protocol):
    name: str

    def send(self, message: Message) -> None: ...


@dataclass
class Delivery:
    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def delivered(self) -> bool:
        return bool(self.succeeded)


class Dispatcher:
    def __init__(self, notifiers: Sequence[Notifier]) -> None:
        self._notifiers = list(notifiers)

    def send(self, message: Message) -> Delivery:
        delivery = Delivery()
        for notifier in self._notifiers:
            try:
                notifier.send(message)
            except NotifyError as exc:
                delivery.failed[notifier.name] = str(exc)
            except Exception as exc:  # a channel bug must not stop the others
                delivery.failed[notifier.name] = f"{type(exc).__name__}: {exc}"
            else:
                delivery.succeeded.append(notifier.name)
        for name, problem in delivery.failed.items():
            log.error("%s notification failed: %s", name, problem)
        if self._notifiers and not delivery.delivered:
            log.error("no notification channel reachable; message lost: %s", message.title)
        return delivery


class ConsoleNotifier:
    """Prints messages instead of sending them (used by --dry-run)."""

    name = "console"

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream = stream

    def send(self, message: Message) -> None:
        stream = self._stream or sys.stdout
        stream.write(message.plain() + "\n\n")
        stream.flush()
