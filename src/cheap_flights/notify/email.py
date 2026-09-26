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
        blocks: list[str] = []
        card: list[str] | None = None

        def close_card() -> None:
            nonlocal card
            if card is not None:
                blocks.append(f'<div class="card" style="{_CARD}">{"".join(card)}</div>')
                card = None

        for line in message.lines:
            text = escape(line.text)
            if line.style == "option":
                close_card()
                card = [f'<div class="option" style="{_OPTION}">{text}</div>']
                continue
            if line.style == "gap":
                close_card()
                continue
            if line.style == "link" and line.url:
                html = (
                    f'<div style="{_ROW}"><a class="button" href="{escape(line.url)}" '
                    f'style="{_BUTTON}">{text}</a></div>'
                )
            else:
                if line.url:
                    text = f'<a href="{escape(line.url)}">{text}</a>'
                muted = _MUTED if line.style in ("detail", "note") else ""
                html = f'<p style="{_P}{muted}">{text}</p>'
            (card if card is not None else blocks).append(html)
        close_card()
        footer = (
            f'<p style="{_P}{_MUTED}font-size:12px;margin-top:16px">{escape(message.footer)}</p>'
            if message.footer
            else ""
        )
        return (
            f'<html><body style="{_BODY}"><div style="max-width:560px">'
            f'<h2 style="font-size:20px;margin:0 0 12px">{escape(message.title)}</h2>'
            f"{''.join(blocks)}{footer}</div></body></html>"
        )


_BODY = "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1f2328;"
_P = "margin:4px 0;"
_MUTED = "color:#656d76;"
_ROW = "margin:10px 0 2px;"
_CARD = "border:1px solid #d0d7de;border-radius:10px;padding:12px 14px;margin:12px 0;"
_OPTION = "font-size:16px;font-weight:600;margin-bottom:4px;"
_BUTTON = (
    "display:inline-block;background:#0969da;color:#ffffff;text-decoration:none;"
    "padding:6px 12px;border-radius:6px;font-size:14px;"
)
