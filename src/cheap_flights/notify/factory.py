"""Build the notifiers a configuration asks for."""

from __future__ import annotations

from ..config import Config
from .base import Notifier
from .email import EmailNotifier
from .telegram import TelegramNotifier


def build_notifiers(config: Config) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if config.notify.telegram is not None:
        telegram = config.notify.telegram
        notifiers.append(TelegramNotifier(telegram.bot_token, telegram.chat_id))
    if config.notify.email is not None:
        notifiers.append(EmailNotifier(config.notify.email))
    return notifiers
