"""Send messages through the Telegram Bot API."""

from __future__ import annotations

from html import escape

import httpx

from .base import Message, NotifyError

API_URL = "https://api.telegram.org"
MAX_LENGTH = 4096


class TelegramNotifier:
    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        client: httpx.Client | None = None,
        retries: int = 1,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=10.0)
        self._retries = retries

    def render(self, message: Message) -> str:
        parts = [f"<b>{escape(message.title[:500], quote=False)}</b>"]
        for line in message.lines:
            text = escape(line.text, quote=False)
            parts.append(f'<a href="{escape(line.url)}">{text}</a>' if line.url else text)
        if message.footer:
            parts.append(f"<i>{escape(message.footer, quote=False)}</i>")
        body = parts[0]
        for part in parts[1:]:
            if len(body) + 1 + len(part) > MAX_LENGTH - 2:
                return body + "\n…"
            body += "\n" + part
        return body

    def send(self, message: Message) -> None:
        payload = {
            "chat_id": self._chat_id,
            "text": self.render(message),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        url = f"{API_URL}/bot{self._token}/sendMessage"
        problem = "no attempt made"
        for _ in range(self._retries + 1):
            try:
                response = self._client.post(url, json=payload)
            except httpx.HTTPError as exc:
                problem = self._hide(f"{type(exc).__name__}: {exc}")
                continue
            if response.status_code == 200:
                return
            problem = self._hide(f"HTTP {response.status_code}: {_description(response)}")
            if response.status_code < 500 and response.status_code != 429:
                raise NotifyError(f"Telegram rejected the message ({problem})")
        raise NotifyError(f"Telegram unreachable ({problem})")

    def _hide(self, text: str) -> str:
        return text.replace(self._token, "***")


def _description(response: httpx.Response) -> str:
    try:
        description = response.json().get("description", "")
    except ValueError:
        description = ""
    return str(description) or response.text[:200]
