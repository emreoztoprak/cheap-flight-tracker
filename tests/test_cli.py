import threading
from pathlib import Path
from types import SimpleNamespace

from cheap_flights import cli
from cheap_flights.notify.base import NotifyError

FIXTURE = Path(__file__).parent / "fixtures" / "google" / "oneway.html"

CONFIG = """
routes:
  - name: ist-lhr
    from: IST
    to: LHR
    trip: one-way
    window: { next_days: 2 }
    alert: { max_price: 100000 }
search: { delay_seconds: 0, retries: 0 }
notify:
  telegram: { bot_token: "123456:abcdefghij", chat_id: "1" }
"""


def write_config(tmp_path, text=CONFIG):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return str(path)


class FakeHttp:
    def __init__(self, text=None, error=None):
        self.text, self.error = text, error

    def get(self, url, params):
        if self.error:
            raise self.error
        return SimpleNamespace(status_code=200, url=url, text=self.text)


def test_config_error_exits_2(tmp_path):
    assert cli.main(["--config", str(tmp_path / "missing.yaml")]) == 2


def test_unknown_place_exits_2(tmp_path):
    path = write_config(tmp_path, CONFIG.replace("to: LHR", "to: Atlantis"))
    assert cli.main(["--config", path]) == 2


def test_dry_run_prints_deals_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_http_client", lambda timeout: FakeHttp(FIXTURE.read_text()))
    data_dir = tmp_path / "data"
    code = cli.main(["--config", write_config(tmp_path), "--dry-run", "--data-dir", str(data_dir)])
    assert code == 0
    assert "✈️ IST → LHR:" in capsys.readouterr().out
    assert not data_dir.exists()  # no state db, no log file


def test_once_with_failing_fetches_exits_1_and_keeps_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "make_http_client", lambda timeout: FakeHttp(error=TimeoutError("timed out"))
    )
    data_dir = tmp_path / "data"
    code = cli.main(["--config", write_config(tmp_path), "--once", "--data-dir", str(data_dir)])
    assert code == 1
    assert (data_dir / "state.db").exists()
    log_text = (data_dir / "logs" / "cheap-flights.log").read_text()
    assert "run finished searches=2" in log_text


def test_test_notify_reports_each_channel(tmp_path, monkeypatch):
    sent = []

    class Good:
        name = "good"

        def send(self, message):
            sent.append(message.title)

    class Bad:
        name = "bad"

        def send(self, message):
            raise NotifyError("nope")

    monkeypatch.setattr(cli, "build_notifiers", lambda config: [Good(), Bad()])
    assert cli.main(["--config", write_config(tmp_path), "--test-notify"]) == 1
    assert sent == ["✅ Cheap Flight Tracker test message"]


def test_second_interrupt_forces_exit():
    stop, exits = threading.Event(), []
    handler = cli._make_signal_handler(stop, exit_now=exits.append)
    handler(2, None)
    assert stop.is_set() and exits == []
    handler(2, None)
    assert exits == [130]
