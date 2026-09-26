from starlette.datastructures import FormData

from cheap_flights.config import Config
from cheap_flights.settings_io import FieldError
from cheap_flights.web import forms


def form(**values):
    items = []
    for key, value in values.items():
        for item in value if isinstance(value, list) else [value]:
            items.append((key, item))
    return FormData(items)


ROUTE_FORM = dict(
    name="mad-bue",
    origin="Madrid",
    to="EZE, AEP",
    trip="round-trip",
    nights_min="10",
    nights_max="10",
    window_mode="next",
    next_days="180",
    date_from="",
    date_to="",
    weekdays=[],
    depart_from="",
    depart_to="",
    stops="any",
    currency="EUR",
    adults="1",
    children="0",
    top_n="3",
    max_airports_per_country="10",
    max_price="900",
    drop_percent="",
)


def test_route_from_form_keeps_only_values_that_differ_from_defaults():
    raw = forms.route_from_form(form(**ROUTE_FORM), defaults={})
    assert raw == {
        "name": "mad-bue",
        "from": "Madrid",
        "to": ["EZE", "AEP"],
        "nights": "10",
        "window": {"next_days": 180},
        "alert": {"max_price": 900},
    }


def test_route_from_form_all_options():
    values = {
        **ROUTE_FORM,
        "trip": "one-way",
        "window_mode": "range",
        "date_from": "2026-11-01",
        "date_to": "2026-12-15",
        "weekdays": ["fri", "sat"],
        "depart_from": "6",
        "depart_to": "14",
        "stops": "direct",
        "currency": "TRY",
        "adults": "2",
        "children": "1",
        "top_n": "5",
        "drop_percent": "12.5",
    }
    raw = forms.route_from_form(form(**values), defaults={"currency": "TRY"})
    assert raw == {
        "name": "mad-bue",
        "from": "Madrid",
        "to": ["EZE", "AEP"],
        "trip": "one-way",
        "window": {"from": "2026-11-01", "to": "2026-12-15"},
        "weekdays": ["fri", "sat"],
        "depart_time": "06:00-14:00",
        "stops": "direct",
        "passengers": {"adults": 2, "children": 1},
        "top_n": 5,
        "alert": {"max_price": 900, "drop_percent": 12.5},
    }


def test_route_to_form_shows_effective_values():
    values = forms.route_to_form(
        {
            "name": "r",
            "from": "IST",
            "to": ["LHR", "DE"],
            "weekdays": ["sat"],
            "alert": {"drop_percent": 20},
        },
        defaults={"currency": "TRY", "nights": "2-4"},
    )
    assert values["to"] == "LHR, DE" and values["currency"] == "TRY"
    assert (values["nights_min"], values["nights_max"]) == ("2", "4")
    assert values["window_mode"] == "next" and values["next_days"] == "90"
    assert values["weekdays"] == ["sat"] and values["drop_percent"] == "20"
    assert values["max_price"] == ""


def test_route_errors_map_to_form_fields():
    errors = [
        FieldError("routes[2].nights", "bad"),
        FieldError("routes.r", "unknown place"),
        FieldError("routes[2].alert", "set one"),
        FieldError("routes[0].name", "other"),
    ]
    fields, general = forms.route_errors(errors, index=2, name="r")
    assert fields == {"nights_min": "bad", "to": "unknown place", "max_price": "set one"}
    assert general == ["routes[0].name: other"]


CONFIG = Config.model_validate(
    {
        "routes": [{"name": "r", "from": "IST", "to": ["LHR"], "alert": {"max_price": 1}}],
        "notify": {
            "telegram": {"bot_token": "123456:current-token", "chat_id": "42"},
            "email": {
                "smtp_host": "smtp.x.com",
                "username": "me@x.com",
                "password": "current-pw",
                "from": "me@x.com",
                "to": ["a@x.com", "b@x.com"],
            },
        },
    }
)


def test_notify_to_form_never_contains_secrets():
    values = forms.notify_to_form(CONFIG)
    assert values["telegram_enabled"] and values["telegram_chat_id"] == "42"
    assert values["telegram_has_token"] and values["email_has_password"]
    assert values["email_to"] == "a@x.com, b@x.com"
    assert "123456:current-token" not in repr(values) and "current-pw" not in repr(values)


def test_notify_from_form_keeps_current_secrets_when_left_blank():
    notify, env, errors = forms.notify_from_form(
        form(
            telegram_enabled="on",
            telegram_bot_token="",
            telegram_chat_id="99",
            email_enabled="on",
            smtp_host="smtp.y.com",
            smtp_port="465",
            smtp_username="me@y.com",
            smtp_password="",
            email_from="me@y.com",
            email_to="c@y.com",
        ),
        CONFIG,
    )
    assert errors == []
    assert notify == {
        "telegram": {"bot_token": "${TELEGRAM_BOT_TOKEN}", "chat_id": "99"},
        "email": {
            "smtp_host": "smtp.y.com",
            "smtp_port": 465,
            "username": "me@y.com",
            "password": "${SMTP_PASSWORD}",
            "from": "me@y.com",
            "to": ["c@y.com"],
        },
    }
    assert env == {"TELEGRAM_BOT_TOKEN": "123456:current-token", "SMTP_PASSWORD": "current-pw"}


def test_notify_from_form_new_secrets_and_disabled_channel():
    notify, env, errors = forms.notify_from_form(
        form(telegram_enabled="on", telegram_bot_token="999:new", telegram_chat_id="1"), None
    )
    assert errors == [] and notify == {
        "telegram": {"bot_token": "${TELEGRAM_BOT_TOKEN}", "chat_id": "1"}
    }
    assert env == {"TELEGRAM_BOT_TOKEN": "999:new", "SMTP_PASSWORD": None}


def test_notify_from_form_requires_a_token_for_a_new_channel():
    _, _, errors = forms.notify_from_form(form(telegram_enabled="on", telegram_chat_id="1"), None)
    assert errors == [FieldError("telegram_bot_token", "enter the bot token")]


def test_settings_round_trip_and_blank_means_built_in_default():
    raw = {
        "routes": [],
        "schedule": "0 8 * * *",
        "search": {"retries": 5},
        "defaults": {"currency": "TRY", "alert": {"max_price": 1}},
    }
    values = forms.settings_to_form(raw)
    assert values["schedule"] == "0 8 * * *" and values["search.retries"] == "5"
    assert values["defaults.currency"] == "TRY" and values["timezone"] == ""
    new = forms.settings_from_form(
        form(
            **{
                "schedule": "0 9 * * *",
                "timezone": "Europe/Madrid",
                "search.retries": "",
                "search.delay_seconds": "1.5",
                "defaults.currency": "",
                "defaults.next_days": "60",
                "defaults.nights": "3-5",
                "logging.level": "DEBUG",
            }
        ),
        raw,
    )
    assert new["schedule"] == "0 9 * * *" and new["timezone"] == "Europe/Madrid"
    assert new["search"] == {"delay_seconds": 1.5}
    assert new["defaults"] == {
        "alert": {"max_price": 1},
        "window": {"next_days": 60},
        "nights": "3-5",
    }
    assert new["logging"] == {"level": "DEBUG"}
    assert new["routes"] == []
