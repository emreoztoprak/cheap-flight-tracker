import stat

import pytest

from cheap_flights.config import ConfigError
from cheap_flights.settings_io import FieldError, SettingsFiles

ROUTE = {"name": "r1", "from": "IST", "to": ["LHR"], "alert": {"max_price": 100}}
TELEGRAM = {"bot_token": "${TELEGRAM_BOT_TOKEN}", "chat_id": "42"}
RAW = {"routes": [ROUTE], "notify": {"telegram": TELEGRAM}}
TOKEN = "123456:secret-token-value"


@pytest.fixture
def files(tmp_path):
    return SettingsFiles(tmp_path / "config.yaml", base_env={})


def test_missing_files(files):
    assert not files.exists()
    assert files.raw() == {}
    assert files.env_file() == {}
    with pytest.raises(ConfigError):
        files.load()


def test_save_writes_both_files_and_keeps_secrets_in_env(files, tmp_path):
    assert files.save(RAW, {"TELEGRAM_BOT_TOKEN": TOKEN}) == []
    config_text = (tmp_path / "config.yaml").read_text()
    assert "${TELEGRAM_BOT_TOKEN}" in config_text and TOKEN not in config_text
    assert (tmp_path / ".env").read_text() == f"TELEGRAM_BOT_TOKEN={TOKEN}\n"
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    loaded = files.load()
    assert loaded.config.notify.telegram.bot_token == TOKEN
    assert [r.route.name for r in loaded.routes] == ["r1"]


def test_second_save_keeps_backups(files, tmp_path):
    files.save(RAW, {"TELEGRAM_BOT_TOKEN": TOKEN})
    files.save({**RAW, "schedule": "0 8 * * *"}, {"SMTP_PASSWORD": "pw-1234"})
    assert "schedule" not in (tmp_path / "config.yaml.bak").read_text()
    assert (tmp_path / ".env.bak").read_text() == f"TELEGRAM_BOT_TOKEN={TOKEN}\n"
    assert files.env_file() == {"TELEGRAM_BOT_TOKEN": TOKEN, "SMTP_PASSWORD": "pw-1234"}


def test_invalid_settings_write_nothing(files, tmp_path):
    bad = {**RAW, "routes": [{**ROUTE, "nights": "9-2"}]}
    errors = files.save(bad, {"TELEGRAM_BOT_TOKEN": TOKEN})
    assert errors and errors[0].path == "routes[0].nights"
    assert "nights must be within" in errors[0].message
    assert not (tmp_path / "config.yaml").exists() and not (tmp_path / ".env").exists()


def test_unknown_place_is_reported_for_the_route(files):
    errors = files.save(
        {**RAW, "routes": [{**ROUTE, "to": ["Atlantis"]}]}, {"TELEGRAM_BOT_TOKEN": TOKEN}
    )
    assert errors[0].path == "routes.r1"
    assert "unknown place 'Atlantis'" in errors[0].message


def test_env_file_overrides_process_environment(tmp_path):
    files = SettingsFiles(
        tmp_path / "config.yaml", base_env={"TELEGRAM_BOT_TOKEN": "old-token-000"}
    )
    files.save(RAW, {"TELEGRAM_BOT_TOKEN": TOKEN})
    assert files.environment()["TELEGRAM_BOT_TOKEN"] == TOKEN


def test_process_environment_still_works_without_env_file(tmp_path):
    files = SettingsFiles(tmp_path / "config.yaml", base_env={"TELEGRAM_BOT_TOKEN": TOKEN})
    assert files.save(RAW, {}) == []
    assert files.load().config.notify.telegram.bot_token == TOKEN


def test_save_text_keeps_comments_and_validates(files, tmp_path):
    files.save(RAW, {"TELEGRAM_BOT_TOKEN": TOKEN})
    text = (tmp_path / "config.yaml").read_text().replace("routes:", "# my routes\nroutes:")
    assert files.save_text(text) == []
    assert "# my routes" in (tmp_path / "config.yaml").read_text()
    assert files.save_text("routes: [") == [FieldError("", files.save_text("routes: [")[0].message)]
    assert "not valid YAML" in files.save_text("routes: [")[0].message


def test_config_file_is_readable_but_env_is_private(files, tmp_path):
    files.save(RAW, {"TELEGRAM_BOT_TOKEN": TOKEN})
    assert stat.S_IMODE((tmp_path / "config.yaml").stat().st_mode) == 0o644
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
