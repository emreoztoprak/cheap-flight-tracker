import smtplib

import pytest

from cheap_flights.config import EmailConfig
from cheap_flights.notify.base import Line, Message, NotifyError
from cheap_flights.notify.email import EmailNotifier

MESSAGE = Message("✈️ Deal <cheap>", (Line("1. 89 EUR", "https://g.test/?a=1&b=2"),), "foot")


def config(**overrides):
    data = {
        "smtp_host": "smtp.example.com",
        "username": "me@example.com",
        "password": "app-password",
        "from": "me@example.com",
        "to": ["you@example.com", "other@example.com"],
        **overrides,
    }
    return EmailConfig.model_validate(data)


class FakeSMTP:
    def __init__(self, error=None):
        self.error, self.logins, self.sent, self.closed = error, [], [], False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def login(self, user, password):
        if self.error:
            raise self.error
        self.logins.append((user, password))

    def send_message(self, message):
        self.sent.append(message)


def connector(smtp):
    calls = []

    def connect(host, port, timeout, use_ssl):
        calls.append((host, port, use_ssl))
        return smtp

    return connect, calls


def test_sends_text_and_html_via_starttls_port():
    smtp = FakeSMTP()
    connect, calls = connector(smtp)
    EmailNotifier(config(), connect).send(MESSAGE)
    assert calls == [("smtp.example.com", 587, False)]
    assert smtp.logins == [("me@example.com", "app-password")]
    (sent,) = smtp.sent
    assert sent["Subject"] == "✈️ Deal <cheap>"
    assert sent["To"] == "you@example.com, other@example.com"
    plain = sent.get_body(("plain",)).get_content()
    html = sent.get_body(("html",)).get_content()
    assert "1. 89 EUR\n   https://g.test/?a=1&b=2" in plain
    assert '<a href="https://g.test/?a=1&amp;b=2">1. 89 EUR</a>' in html
    assert "&lt;cheap&gt;" in html
    assert smtp.closed


def test_port_465_uses_ssl():
    connect, calls = connector(FakeSMTP())
    EmailNotifier(config(smtp_port=465), connect).send(MESSAGE)
    assert calls == [("smtp.example.com", 465, True)]


def test_no_login_without_credentials():
    smtp = FakeSMTP()
    connect, _ = connector(smtp)
    EmailNotifier(config(username=None, password=None), connect).send(MESSAGE)
    assert smtp.logins == [] and len(smtp.sent) == 1


def test_smtp_errors_become_notify_errors():
    smtp = FakeSMTP(smtplib.SMTPAuthenticationError(535, b"bad credentials"))
    connect, _ = connector(smtp)
    with pytest.raises(NotifyError, match="smtp.example.com:587 failed: SMTPAuthenticationError"):
        EmailNotifier(config(), connect).send(MESSAGE)


def test_connection_errors_become_notify_errors():
    def refuse(host, port, timeout, use_ssl):
        raise ConnectionRefusedError("refused")

    with pytest.raises(NotifyError, match="ConnectionRefusedError"):
        EmailNotifier(config(), refuse).send(MESSAGE)


def test_report_layout_in_plain_text_and_html():
    from tests.test_telegram import REPORT

    smtp = FakeSMTP()
    EmailNotifier(config(), connector(smtp)[0]).send(REPORT)
    (sent,) = smtp.sent
    plain = sent.get_body(("plain",)).get_content()
    assert plain == (
        "🔥 r1 — 80 €\n\n"
        "➖ Same as last check\n"
        "IST → LHR · one way\n"
        "\n"
        "🥇 80 € · Mon 02 Nov\n"
        "      Pegasus · direct · 08:00 → 10:30\n"
        "      View on Google Flights: https://g.test/?a=1&b=2\n"
        "\n"
        "Checked Sat 26 Sep 22:40\n"
    )
    html = sent.get_body(("html",)).get_content()
    assert "<h2" in html and "🔥 r1 — 80 €</h2>" in html
    assert 'class="option"' in html and "🥇 80 € · Mon 02 Nov" in html
    assert '<a class="button" href="https://g.test/?a=1&amp;b=2"' in html
    assert html.count('class="option"') == 1
