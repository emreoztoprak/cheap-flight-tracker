from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cheap_flights.coordinator import Coordinator
from cheap_flights.logging_setup import LogBuffer, configure_logging
from cheap_flights.models import FetchKind, FetchResult
from cheap_flights.notify.base import NotifyError
from cheap_flights.settings_io import SettingsFiles
from cheap_flights.store import Store
from cheap_flights.web.app import create_app
from tests.helpers import make_offer

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
TOKEN = "123456:very-secret-token"
PASSWORD = "very-secret-password"
RAW = {
    "schedule": "0 8 * * *",
    "search": {"delay_seconds": 0, "retries": 0},
    "routes": [
        {
            "name": "ist-lon",
            "from": "IST",
            "to": ["LHR"],
            "trip": "one-way",
            "window": {"next_days": 2},
            "alert": {"max_price": 100},
        }
    ],
    "notify": {
        "telegram": {"bot_token": "${TELEGRAM_BOT_TOKEN}", "chat_id": "42"},
        "email": {
            "smtp_host": "smtp.x.com",
            "username": "me@x.com",
            "password": "${SMTP_PASSWORD}",
            "from": "me@x.com",
            "to": ["me@x.com"],
        },
    },
}
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "SMTP_PASSWORD": PASSWORD}


class Inbox:
    name = "inbox"

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class Fetcher:
    def search(self, job):
        return FetchResult(FetchKind.OK, (make_offer(80, route="ist-lon", depart=job.depart_date),))


@pytest.fixture
def env(tmp_path):
    files = SettingsFiles(tmp_path / "config.yaml", base_env={})
    inbox = Inbox()
    coordinator = Coordinator(
        files,
        Store(":memory:"),
        fetcher_factory=lambda config: Fetcher(),
        notifier_factory=lambda config: [inbox],
        clock=lambda: NOW,
        sleep=lambda s: None,
    )
    coordinator.reload()
    tested = []

    def make_notifier(channel, settings):
        tested.append((channel, settings))
        if settings.get("chat_id") == "fail":

            class Broken:
                name = channel

                def send(self, message):
                    raise NotifyError("chat not found")

            return Broken()
        return Inbox()

    buffer = LogBuffer()
    configure_logging("INFO", "text", [TOKEN, PASSWORD], buffer=buffer)
    app = create_app(
        coordinator,
        buffer,
        make_notifier=make_notifier,
        clock=lambda: NOW,
        allowed_hosts=["testserver"],
    )
    client = TestClient(app, headers={"Origin": "http://testserver"})
    return client, coordinator, files, inbox, tested


def configured(env):
    client, coordinator, files, *_ = env
    assert coordinator.save(RAW, ENV) == []
    return env


def test_health_endpoint(env):
    assert env[0].get("/healthz").json() == {"ok": True}


def test_first_start_shows_setup(env):
    client = env[0]
    page = client.get("/")
    assert page.status_code == 200 and "Welcome" in page.text and "/setup" in page.text
    assert client.get("/setup").status_code == 200


def test_setup_creates_the_config(env):
    client, coordinator, files, *_ = env
    response = client.post(
        "/setup",
        data={
            "telegram_enabled": "on",
            "telegram_bot_token": TOKEN,
            "telegram_chat_id": "7",
            "name": "mad-ist",
            "origin": "Madrid",
            "to": "IST",
            "trip": "round-trip",
            "nights_min": "7",
            "nights_max": "7",
            "window_mode": "next",
            "next_days": "30",
            "max_price": "300",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/?saved=1"
    assert coordinator.loaded.config.routes[0].name == "mad-ist"
    assert files.env_file() == {"TELEGRAM_BOT_TOKEN": TOKEN}


def test_setup_with_errors_shows_them(env):
    client, coordinator, *_ = env
    page = client.post(
        "/setup",
        data={
            "telegram_enabled": "on",
            "telegram_chat_id": "7",
            "name": "x",
            "origin": "IST",
            "to": "Atlantis",
            "window_mode": "next",
            "next_days": "30",
            "max_price": "1",
        },
    )
    assert page.status_code == 200 and "enter the bot token" in page.text
    assert coordinator.loaded is None


def test_dashboard_shows_routes_and_runs(env):
    client, coordinator, *_ = configured(env)
    page = client.get("/")
    assert "ist-lon" in page.text and "Run now" in page.text
    assert client.post("/run").status_code == 200
    coordinator.wait_idle(5)
    page = client.get("/")
    assert "80 EUR" in page.text
    status = client.get("/partials/status")
    assert "2 searches" in status.text


def test_route_crud(env):
    client, coordinator, files, *_ = configured(env)
    assert "ist-lon" in client.get("/routes").text
    form = client.get("/routes/0").text
    assert 'value="ist-lon"' in form
    new_route = {
        "name": "ist-ams",
        "origin": "IST",
        "to": "AMS",
        "trip": "one-way",
        "window_mode": "next",
        "next_days": "10",
        "stops": "direct",
        "currency": "EUR",
        "adults": "1",
        "children": "0",
        "top_n": "3",
        "max_airports_per_country": "10",
        "max_price": "150",
    }
    response = client.post("/routes/new", data=new_route, follow_redirects=False)
    assert response.status_code == 303
    assert [r.name for r in coordinator.loaded.config.routes] == ["ist-lon", "ist-ams"]
    assert client.post("/routes/1/duplicate", follow_redirects=False).status_code == 303
    assert coordinator.loaded.config.routes[2].name == "ist-ams-copy"
    assert client.post("/routes/2/delete", follow_redirects=False).status_code == 303
    assert len(coordinator.loaded.config.routes) == 2


def test_route_without_highlight_rule_is_saved_without_alert(env):
    client, coordinator, files, *_ = configured(env)
    route = {
        "name": "ist-ams",
        "origin": "IST",
        "to": "AMS",
        "trip": "one-way",
        "window_mode": "next",
        "next_days": "10",
    }
    assert client.post("/routes/new", data=route, follow_redirects=False).status_code == 303
    saved = coordinator.loaded.config.routes[1]
    assert (saved.alert.max_price, saved.alert.drop_percent) == (None, None)
    assert "alert" not in files.raw_text().split("ist-ams")[1]
    assert "—" in client.get("/routes").text


def test_invalid_route_is_not_saved(env):
    client, coordinator, *_ = configured(env)
    page = client.post(
        "/routes/0",
        data={
            "name": "ist-lon",
            "origin": "IST",
            "to": "Atlantis",
            "trip": "one-way",
            "window_mode": "next",
            "next_days": "10",
            "max_price": "1",
        },
    )
    assert page.status_code == 200 and "unknown place" in page.text
    assert coordinator.loaded.config.routes[0].to == ("LHR",)


def test_cannot_delete_the_last_route(env):
    client, coordinator, *_ = configured(env)
    page = client.post("/routes/0/delete")
    assert "at least one route" in page.text
    assert len(coordinator.loaded.config.routes) == 1


def test_no_page_shows_secrets(env):
    client, coordinator, *_ = configured(env)
    coordinator.run("manual")
    for path in (
        "/",
        "/routes",
        "/routes/0",
        "/routes/new",
        "/notifications",
        "/settings",
        "/history",
        "/partials/status",
        "/partials/logs?level=DEBUG",
    ):
        text = client.get(path).text
        assert TOKEN not in text and PASSWORD not in text, path


def test_notifications_save_and_keep_secrets(env):
    client, coordinator, files, *_ = configured(env)
    response = client.post(
        "/notifications",
        data={
            "telegram_enabled": "on",
            "telegram_bot_token": "",
            "telegram_chat_id": "43",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert coordinator.loaded.config.notify.telegram.chat_id == "43"
    assert coordinator.loaded.config.notify.telegram.bot_token == TOKEN
    assert coordinator.loaded.config.notify.email is None


def test_test_notification_uses_form_values(env):
    client, _, _, _, tested = configured(env)
    ok = client.post("/notifications/test/telegram", data={"telegram_chat_id": "99"})
    assert "Sent" in ok.text and tested[-1] == ("telegram", {"bot_token": TOKEN, "chat_id": "99"})
    bad = client.post("/notifications/test/telegram", data={"telegram_chat_id": "fail"})
    assert "chat not found" in bad.text


def test_settings_save_and_errors(env):
    client, coordinator, *_ = configured(env)
    ok = client.post(
        "/settings",
        data={"schedule": "0 9 * * *", "timezone": "Europe/Madrid"},
        follow_redirects=False,
    )
    assert ok.status_code == 303 and coordinator.schedule()[0] == "0 9 * * *"
    bad = client.post("/settings", data={"schedule": "nope", "timezone": "Europe/Madrid"})
    assert "not a valid cron expression" in bad.text
    assert coordinator.schedule()[0] == "0 9 * * *"


def test_raw_yaml_editor(env):
    client, coordinator, files, *_ = configured(env)
    text = files.raw_text().replace("0 8 * * *", "0 7 * * *")
    assert (
        client.post("/settings/raw", data={"text": text}, follow_redirects=False).status_code == 303
    )
    assert coordinator.schedule()[0] == "0 7 * * *"
    assert "not valid YAML" in client.post("/settings/raw", data={"text": "a: ["}).text


def test_cron_preview_defaults_to_madrid_time(env):
    client = configured(env)[0]
    # NOW is 12:00 UTC = 14:00 in Madrid: 13:00 today has passed in Madrid (not in UTC)
    preview = client.get("/settings/cron", params={"schedule": "0 13 * * *", "timezone": ""})
    assert "Next runs (Europe/Madrid): Sun 27 Sep 13:00" in preview.text


def test_cron_preview(env):
    client = configured(env)[0]
    preview = client.get("/settings/cron", params={"schedule": "0 8 * * *", "timezone": "UTC"})
    assert "08:00" in preview.text
    assert (
        "not a valid"
        in client.get("/settings/cron", params={"schedule": "x", "timezone": "UTC"}).text
    )


def test_history_data_and_logs(env):
    client, coordinator, *_ = configured(env)
    coordinator.run("manual")
    data = client.get("/history/data/ist-lon").json()
    assert data["datasets"][0]["label"] == "LHR" and data["datasets"][0]["data"] == [80]
    assert "80" in client.get("/history").text
    assert "run finished" in client.get("/partials/logs", params={"level": "INFO"}).text


def test_place_suggestions_and_search_estimate(env):
    client = configured(env)[0]
    assert "LHR" in client.get("/places", params={"q": "heathrow"}).text
    estimate = client.post(
        "/routes/estimate",
        data={
            "origin": "IST",
            "to": "LHR, AMS",
            "trip": "round-trip",
            "nights_min": "3",
            "nights_max": "4",
            "window_mode": "next",
            "next_days": "10",
            "max_price": "1",
        },
    )
    assert "<strong>40</strong> searches" in estimate.text


def test_cross_site_posts_are_refused(env):
    client, coordinator, *_ = configured(env)
    response = client.post(
        "/settings", data={"schedule": "0 1 * * *"}, headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403
    assert coordinator.schedule()[0] == "0 8 * * *"


def test_run_duration_is_readable():
    from datetime import timedelta

    from cheap_flights.store import RunRecord
    from cheap_flights.web.views import duration

    assert duration(RunRecord(NOW, NOW + timedelta(seconds=3), "manual", 1, 1, 0)) == "3 s"
    assert duration(RunRecord(NOW, NOW + timedelta(minutes=5), "manual", 1, 1, 0)) == "5 min"


def test_place_suggestions_use_the_fields_own_value(env):
    client = configured(env)[0]
    to = client.get("/places", params={"to": "EZE, Lond", "target": "to"})
    assert 'data-place="London"' in to.text
    origin = client.get("/places", params={"origin": "heathr", "target": "origin"})
    assert 'data-place="LHR"' in origin.text


def test_history_chart_covers_the_configured_period(env):
    from datetime import timedelta

    client, coordinator, *_ = configured(env)
    coordinator.store.add_price("ist-lon", "LHR", NOW - timedelta(days=200), 70, "EUR")
    assert client.get("/history/data/ist-lon").json()["datasets"] == []
    assert coordinator.save({**RAW, "history_days": 365}, {}) == []
    assert client.get("/history/data/ist-lon").json()["datasets"][0]["data"] == [70]
    assert "last 365 days" in client.get("/history").text


def test_history_days_is_saved_from_the_settings_page(env):
    client, coordinator, *_ = configured(env)
    ok = client.post("/settings", data={"history_days": "365"}, follow_redirects=False)
    assert ok.status_code == 303 and coordinator.loaded.config.history_days == 365
    bad = client.post("/settings", data={"history_days": "10"})
    assert "greater than or equal to 31" in bad.text
    assert coordinator.loaded.config.history_days == 365
