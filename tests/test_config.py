from datetime import date
from pathlib import Path

import pytest

from cheap_flights.config import ConfigError, Window, load_config

BASE = """
routes:
  - name: ist-lon
    from: IST
    to: London
    alert: { max_price: 120 }
notify:
  telegram: { bot_token: "${TG_TOKEN}", chat_id: "${TG_CHAT}" }
"""
ENV = {"TG_TOKEN": "123456:abcdefghij", "TG_CHAT": "42"}


def load(tmp_path: Path, text: str, env: dict[str, str] | None = None):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return load_config(path, ENV if env is None else env)


def with_route(extra: str) -> str:
    return f"""
routes:
  - name: r
    from: IST
    to: [AMS]
    alert: {{ max_price: 100 }}
{extra}
notify:
  telegram: {{ bot_token: "123456:abcdefghij", chat_id: "1" }}
"""


def test_minimal_config_gets_defaults(tmp_path):
    config = load(tmp_path, BASE)
    route = config.routes[0]
    assert route.origin == "IST"
    assert route.to == ("London",)
    assert route.trip == "round-trip"
    assert route.nights == (3, 7)
    assert route.window.next_days == 90
    assert route.currency == "EUR"
    assert route.max_stops is None
    assert route.top_n == 3
    assert config.schedule == "0 */6 * * *"
    assert config.search.max_searches_per_run == 2000
    assert config.notify.telegram.chat_id == "42"
    assert config.secrets() == ["123456:abcdefghij"]


def test_defaults_are_inherited_and_overridden(tmp_path):
    text = """
defaults:
  currency: TRY
  stops: direct
  alert: { max_price: 50 }
routes:
  - { name: a, from: IST, to: [AMS] }
  - { name: b, from: IST, to: [DE], currency: EUR, alert: { drop_percent: 20 } }
notify:
  telegram: { bot_token: "123456:abcdefghij", chat_id: 1 }
"""
    a, b = load(tmp_path, text, env={}).routes
    assert (a.currency, a.max_stops, a.alert.max_price) == ("TRY", 0, 50)
    assert (b.currency, b.alert.max_price, b.alert.drop_percent) == ("EUR", None, 20)


def test_missing_env_var_names_the_variable_and_field(tmp_path):
    with pytest.raises(
        ConfigError, match=r"notify\.telegram\.chat_id: environment variable TG_CHAT is not set"
    ):
        load(tmp_path, BASE, env={"TG_TOKEN": "123456:abcdefghij"})


def test_env_overrides(tmp_path):
    env = {**ENV, "CFR_SCHEDULE": "*/30 * * * *", "CFR_LOG_LEVEL": "debug"}
    config = load(tmp_path, BASE, env=env)
    assert config.schedule == "*/30 * * * *"
    assert config.logging.level == "DEBUG"


@pytest.mark.parametrize(
    ("snippet", "error"),
    [
        ("schedule: 'every hour'", "schedule: not a valid cron expression"),
        ("timezone: Mars/Base", "timezone: unknown time zone"),
    ],
)
def test_invalid_top_level_settings(tmp_path, snippet, error):
    with pytest.raises(ConfigError, match=error):
        load(tmp_path, BASE + snippet + "\n")


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ("    trip: one-way\n    nights: 3-5", r"routes\[0\]\.nights: only used for round trips"),
        (
            "    window: { next_days: 30, from: 2026-11-01, to: 2026-11-05 }",
            "use either next_days or from/to",
        ),
        ("    window: { from: 2026-12-01, to: 2026-11-01 }", "from must be on or before to"),
        ("    nights: 7-3", "nights must be within 1-30"),
        ("    depart_time: morning", "depart_time must look like"),
        ("    weekdays: [funday]", "unknown weekday"),
        ("    alert: {}", "set max_price, drop_percent, or both"),
        ("    to: [NO]", "quote it"),
    ],
)
def test_invalid_route_settings(tmp_path, extra, error):
    with pytest.raises(ConfigError, match=error):
        load(tmp_path, with_route(extra), env={})


def test_route_value_parsing(tmp_path):
    text = with_route(
        '    nights: "5"\n    depart_time: "06:00-14:00"\n'
        "    weekdays: [Sat, fri]\n    stops: max_1"
    )
    route = load(tmp_path, text, env={}).routes[0]
    assert route.nights == (5, 5)
    assert route.depart_time == (6, 14)
    assert route.weekdays == (4, 5)
    assert route.max_stops == 1


def test_duplicate_route_names_rejected(tmp_path):
    text = """
routes:
  - { name: r, from: IST, to: AMS, alert: { max_price: 1 } }
  - { name: r, from: IST, to: LHR, alert: { max_price: 1 } }
notify:
  telegram: { bot_token: "123456:abcdefghij", chat_id: "1" }
"""
    with pytest.raises(ConfigError, match="duplicate route name 'r'"):
        load(tmp_path, text, env={})


def test_notify_needs_a_channel(tmp_path):
    text = """
routes:
  - { name: r, from: IST, to: AMS, alert: { max_price: 1 } }
notify: {}
"""
    with pytest.raises(ConfigError, match="configure telegram, email, or both"):
        load(tmp_path, text, env={})


def test_email_settings(tmp_path):
    text = """
routes:
  - { name: r, from: IST, to: AMS, alert: { max_price: 1 } }
notify:
  email:
    smtp_host: smtp.example.com
    username: me@example.com
    password: s3cret-pass
    from: me@example.com
    to: you@example.com
"""
    config = load(tmp_path, text, env={})
    email = config.notify.email
    assert (email.smtp_port, email.sender, email.to) == (
        587,
        "me@example.com",
        ("you@example.com",),
    )
    assert config.secrets() == ["s3cret-pass"]


def test_unreadable_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config file"):
        load_config(tmp_path / "missing.yaml", {})


def test_window_dates():
    today = date(2026, 9, 26)
    assert Window(next_days=3).dates(today) == [
        date(2026, 9, 27),
        date(2026, 9, 28),
        date(2026, 9, 29),
    ]
    fixed = Window.model_validate({"from": "2026-09-20", "to": "2026-09-28"})
    assert fixed.dates(today) == [date(2026, 9, 27), date(2026, 9, 28)]
    past = Window.model_validate({"from": "2026-09-01", "to": "2026-09-10"})
    assert past.dates(today) == []


def test_unquoted_single_country_code_gets_the_quote_hint(tmp_path):
    with pytest.raises(ConfigError, match="quote it"):
        load(tmp_path, with_route("    to: NO"), env={})


def test_unquoted_origin_gets_the_quote_hint(tmp_path):
    with pytest.raises(ConfigError, match="quote it"):
        load(tmp_path, with_route("    from: NO"), env={})


def test_log_file_defaults(tmp_path):
    logging = load(tmp_path, BASE).logging
    assert (logging.file, logging.max_size_mb, logging.backups) == ("logs/cheap-flights.log", 10, 5)


def test_log_file_can_be_turned_off(tmp_path):
    config = load(tmp_path, BASE + 'logging: { file: "", backups: 0 }\n')
    assert (config.logging.file, config.logging.backups) == (None, 0)


def test_log_file_size_must_be_positive(tmp_path):
    with pytest.raises(ConfigError, match=r"logging\.max_size_mb"):
        load(tmp_path, BASE + "logging: { max_size_mb: 0 }\n")


def test_history_days_default_and_limits(tmp_path):
    assert load(tmp_path, BASE).history_days == 90
    assert load(tmp_path, BASE + "history_days: 365\n").history_days == 365
    for bad in ("30", "4000"):
        with pytest.raises(ConfigError, match="history_days"):
            load(tmp_path, BASE + f"history_days: {bad}\n")
