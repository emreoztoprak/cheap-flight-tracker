import json

import httpx
import pytest

from cheap_flights.notify.base import Line, Message, NotifyError
from cheap_flights.notify.telegram import MAX_LENGTH, TelegramNotifier

TOKEN = "123456:secret-token"


def client(*responses):
    calls, queue = [], list(responses)

    def handler(request):
        calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_sends_html_message():
    http, calls = client(httpx.Response(200, json={"ok": True}))
    message = Message("✈️ Deal", (Line("1. 89 EUR", "https://g.test/?a=1&b=2"),), "footer")
    TelegramNotifier(TOKEN, "42", http).send(message)
    (request,) = calls
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert body["chat_id"] == "42"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is True
    assert body["text"] == (
        '<b>✈️ Deal</b>\n<a href="https://g.test/?a=1&amp;b=2">1. 89 EUR</a>\n<i>footer</i>'
    )


def test_escapes_special_characters():
    notifier = TelegramNotifier(TOKEN, "42", client()[0])
    text = notifier.render(Message("A & B <x>", (Line("Air <&> Co"),)))
    assert text == "<b>A &amp; B &lt;x&gt;</b>\nAir &lt;&amp;&gt; Co"


def test_long_messages_are_cut_on_line_boundaries():
    notifier = TelegramNotifier(TOKEN, "42", client()[0])
    lines = tuple(Line(f"line {i} " + "x" * 90) for i in range(100))
    text = notifier.render(Message("title", lines))
    assert len(text) <= MAX_LENGTH
    assert text.endswith("\n…")
    assert all(part.startswith(("line", "<b>", "…")) for part in text.split("\n"))


def test_client_error_is_not_retried():
    http, calls = client(httpx.Response(400, json={"ok": False, "description": "chat not found"}))
    with pytest.raises(NotifyError, match="chat not found"):
        TelegramNotifier(TOKEN, "42", http).send(Message("t"))
    assert len(calls) == 1


def test_server_error_is_retried_once():
    http, calls = client(httpx.Response(502, text="bad gateway"), httpx.Response(200, json={}))
    TelegramNotifier(TOKEN, "42", http).send(Message("t"))
    assert len(calls) == 2


def test_connection_errors_never_leak_the_token():
    error = httpx.ConnectError(f"failed to reach https://api.telegram.org/bot{TOKEN}/sendMessage")
    http, _ = client(error, error)
    with pytest.raises(NotifyError) as info:
        TelegramNotifier(TOKEN, "42", http).send(Message("t"))
    assert TOKEN not in str(info.value)
    assert "Telegram unreachable" in str(info.value)
