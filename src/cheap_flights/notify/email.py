"""Send messages by SMTP (STARTTLS, or implicit TLS on port 465)."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from html import escape
from typing import Any

from ..config import EmailConfig
from .base import Message, NotifyError

Connect = Callable[[str, int, float, bool], Any]


def _connect(host: str, port: int, timeout: float, use_ssl: bool) -> smtplib.SMTP:
    context = ssl.create_default_context()
    if use_ssl:
        return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    smtp = smtplib.SMTP(host, port, timeout=timeout)
    try:
        smtp.starttls(context=context)
    except Exception:
        smtp.close()
        raise
    return smtp


class EmailNotifier:
    name = "email"

    def __init__(
        self, config: EmailConfig, connect: Connect | None = None, timeout: float = 20.0
    ) -> None:
        self._config = config
        self._connect = connect or _connect
        self._timeout = timeout

    def build(self, message: Message) -> EmailMessage:
        email = EmailMessage()
        email["Subject"] = message.title
        email["From"] = self._config.sender
        email["To"] = ", ".join(self._config.to)
        email.set_content(message.plain())
        email.add_alternative(self._html(message), subtype="html")
        return email

    def send(self, message: Message) -> None:
        config = self._config
        email = self.build(message)
        try:
            with self._connect(
                config.smtp_host, config.smtp_port, self._timeout, config.smtp_port == 465
            ) as smtp:
                if config.username and config.password:
                    smtp.login(config.username, config.password)
                smtp.send_message(email)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifyError(
                f"email via {config.smtp_host}:{config.smtp_port} failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _html(message: Message) -> str:
        items = "".join(
            f'<li><a href="{escape(line.url)}">{escape(line.text)}</a></li>'
            if line.url
            else f"<li>{escape(line.text)}</li>"
            for line in message.lines
        )
        footer = f"<p><em>{escape(message.footer)}</em></p>" if message.footer else ""
        return (
            f"<html><body><h3>{escape(message.title)}</h3>"
            f'<ul style="font-family: monospace">{items}</ul>{footer}</body></html>'
        )
