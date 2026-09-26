import io

from cheap_flights.notify.base import ConsoleNotifier, Dispatcher, Line, Message, NotifyError

MESSAGE = Message("title", (Line("a", "https://x.test"),), "foot")


class Recorder:
    def __init__(self, name, error=None):
        self.name, self.error, self.sent = name, error, []

    def send(self, message):
        if self.error:
            raise self.error
        self.sent.append(message)


def test_dispatch_to_all_channels():
    tg, mail = Recorder("telegram"), Recorder("email")
    delivery = Dispatcher([tg, mail]).send(MESSAGE)
    assert delivery.delivered and delivery.succeeded == ["telegram", "email"]
    assert tg.sent == [MESSAGE] and mail.sent == [MESSAGE]


def test_one_failing_channel_does_not_stop_the_other():
    mail = Recorder("email")
    delivery = Dispatcher([Recorder("telegram", NotifyError("bad token")), mail]).send(MESSAGE)
    assert delivery.delivered
    assert delivery.failed == {"telegram": "bad token"}
    assert mail.sent == [MESSAGE]


def test_unexpected_exceptions_are_contained():
    delivery = Dispatcher([Recorder("telegram", RuntimeError("boom"))]).send(MESSAGE)
    assert not delivery.delivered
    assert delivery.failed == {"telegram": "RuntimeError: boom"}


def test_console_notifier_prints_plain_text():
    stream = io.StringIO()
    ConsoleNotifier(stream).send(MESSAGE)
    assert stream.getvalue() == "title\n\na\n   https://x.test\n\nfoot\n\n"
