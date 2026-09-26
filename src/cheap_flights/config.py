"""Load and validate the YAML configuration file."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from croniter import croniter
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class ConfigError(Exception):
    """The configuration is missing, unreadable or invalid."""


DEFAULT_TIMEZONE = "Europe/Madrid"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _range(value: Any, *, low: int, high: int, what: str) -> tuple[int, int]:
    """Parse 5, "5", "3-7" or (3, 7) into an inclusive (low, high) pair."""
    problem = f"expected a number or range like 3-7 for {what}, got {value!r}"
    if isinstance(value, bool):
        raise ValueError(problem)
    try:
        if isinstance(value, int):
            a = b = value
        elif isinstance(value, str):
            parts = value.replace(" ", "").split("-")
            if len(parts) == 1:
                a = b = int(parts[0])
            elif len(parts) == 2:
                a, b = int(parts[0]), int(parts[1])
            else:
                raise ValueError(problem)
        elif isinstance(value, list | tuple) and len(value) == 2:
            a, b = int(value[0]), int(value[1])
        else:
            raise ValueError(problem)
    except (TypeError, ValueError):
        raise ValueError(problem) from None
    if not low <= a <= b <= high:
        raise ValueError(f"{what} must be within {low}-{high} and written low-high, got {value!r}")
    return (a, b)


def _hours(value: Any) -> tuple[int, int]:
    """Parse "06:00-14:00" (or "6-14") into departure hours; Google filters by whole hours."""
    problem = f'depart_time must look like "06:00-14:00", got {value!r}'
    if not isinstance(value, str) or value.count("-") != 1:
        raise ValueError(problem)
    start, end = value.replace(" ", "").split("-")
    try:
        hours = (int(start.split(":")[0]), int(end.split(":")[0]))
    except ValueError:
        raise ValueError(problem) from None
    return _range(hours, low=0, high=23, what="depart_time")


_QUOTE_HINT = 'YAML read a code as true/false; quote it, e.g. "NO" for Norway'


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class Window(_Model):
    next_days: int | None = Field(default=None, ge=1, le=330)
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")

    @model_validator(mode="after")
    def _one_mode(self) -> Window:
        fixed = self.start is not None or self.end is not None
        if self.next_days is not None and fixed:
            raise ValueError("use either next_days or from/to, not both")
        if self.next_days is None and (self.start is None or self.end is None):
            raise ValueError("set next_days, or both from and to")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("from must be on or before to")
        return self

    def dates(self, today: date) -> list[date]:
        """Departure dates to search, never earlier than tomorrow."""
        first = today + timedelta(days=1)
        if self.next_days is not None:
            return [first + timedelta(days=i) for i in range(self.next_days)]
        assert self.start is not None and self.end is not None
        start = max(self.start, first)
        return [start + timedelta(days=i) for i in range((self.end - start).days + 1)]


class Passengers(_Model):
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def _max_nine(self) -> Passengers:
        if self.adults + self.children > 9:
            raise ValueError("at most 9 passengers")
        return self


class AlertRule(_Model):
    """Optional highlights: a check's message is marked 🔥 when one of these is met."""

    max_price: int | None = Field(default=None, gt=0)
    drop_percent: float | None = Field(default=None, gt=0, lt=100)


class Route(_Model):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    origin: str = Field(alias="from", min_length=2)
    to: tuple[str, ...] = Field(min_length=1)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    trip: Literal["round-trip", "one-way"] = "round-trip"
    nights: tuple[int, int] = (3, 7)
    window: Window = Window(next_days=90)
    stops: Literal["direct", "max_1", "any"] = "any"
    passengers: Passengers = Passengers()
    top_n: int = Field(default=3, ge=1, le=10)
    max_airports_per_country: int = Field(default=10, ge=1, le=50)
    depart_time: tuple[int, int] | None = None
    weekdays: tuple[int, ...] | None = None
    alert: AlertRule = AlertRule()

    @field_validator("origin", mode="before")
    @classmethod
    def _origin_text(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError(_QUOTE_HINT)
        return value

    @field_validator("to", mode="before")
    @classmethod
    def _to_list(cls, value: Any) -> Any:
        items = [value] if isinstance(value, str | bool) else value
        if isinstance(items, list | tuple) and any(isinstance(item, bool) for item in items):
            raise ValueError(_QUOTE_HINT)
        return items

    @field_validator("nights", mode="before")
    @classmethod
    def _nights(cls, value: Any) -> tuple[int, int]:
        return _range(value, low=1, high=30, what="nights")

    @field_validator("depart_time", mode="before")
    @classmethod
    def _depart_time(cls, value: Any) -> tuple[int, int] | None:
        return None if value is None else _hours(value)

    @field_validator("weekdays", mode="before")
    @classmethod
    def _weekdays(cls, value: Any) -> tuple[int, ...] | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list | tuple) or not value:
            raise ValueError("weekdays must be a non-empty list like [fri, sat]")
        days = set()
        for item in value:
            key = str(item).strip().lower()[:3]
            if key not in WEEKDAYS:
                raise ValueError(f"unknown weekday {item!r}; use mon, tue, wed, thu, fri, sat, sun")
            days.add(WEEKDAYS.index(key))
        return tuple(sorted(days))

    @property
    def max_stops(self) -> int | None:
        return {"direct": 0, "max_1": 1, "any": None}[self.stops]


class SearchSettings(_Model):
    delay_seconds: float = Field(default=1.0, ge=0, le=60)
    max_searches_per_run: int = Field(default=2000, ge=1, le=20000)
    retries: int = Field(default=3, ge=0, le=10)
    timeout_seconds: float = Field(default=20.0, gt=0, le=120)


class TelegramConfig(_Model):
    bot_token: str = Field(min_length=10)
    chat_id: str = Field(min_length=1)

    @field_validator("chat_id", mode="before")
    @classmethod
    def _chat_id_text(cls, value: Any) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


class EmailConfig(_Model):
    smtp_host: str = Field(min_length=1)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    sender: str = Field(alias="from", pattern=r".+@.+")
    to: tuple[str, ...] = Field(min_length=1)

    @field_validator("to", mode="before")
    @classmethod
    def _to_list(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value

    @field_validator("to")
    @classmethod
    def _addresses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for address in value:
            if "@" not in address:
                raise ValueError(f"not an email address: {address!r}")
        return value

    @model_validator(mode="after")
    def _credentials(self) -> EmailConfig:
        if (self.username is None) != (self.password is None):
            raise ValueError("set both username and password, or neither")
        return self


class NotifyConfig(_Model):
    telegram: TelegramConfig | None = None
    email: EmailConfig | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> NotifyConfig:
        if self.telegram is None and self.email is None:
            raise ValueError("configure telegram, email, or both")
        return self


class HealthConfig(_Model):
    alert_after_failed_runs: int = Field(default=2, ge=1, le=100)
    reminder_hours: float = Field(default=24.0, gt=0)


class LoggingConfig(_Model):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["text", "json"] = "text"
    file: str | None = "logs/cheap-flights.log"  # relative to the data dir; "" disables
    max_size_mb: float = Field(default=10, gt=0, le=1000)
    backups: int = Field(default=5, ge=0, le=100)

    @field_validator("level", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("file", mode="before")
    @classmethod
    def _blank_is_off(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value.strip() if isinstance(value, str) else value


class Config(_Model):
    schedule: str = "0 */6 * * *"
    timezone: str = DEFAULT_TIMEZONE
    history_days: int = Field(default=90, ge=31, le=3650)  # the drop rule needs 30 days
    search: SearchSettings = SearchSettings()
    routes: tuple[Route, ...] = Field(min_length=1)
    notify: NotifyConfig
    health: HealthConfig = HealthConfig()
    logging: LoggingConfig = LoggingConfig()

    @field_validator("schedule")
    @classmethod
    def _cron(cls, value: str) -> str:
        if not croniter.is_valid(value):
            raise ValueError(f"not a valid cron expression: {value!r}")
        return value

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {value!r}") from None
        return value

    @model_validator(mode="after")
    def _unique_names(self) -> Config:
        seen: set[str] = set()
        for route in self.routes:
            if route.name in seen:
                raise ValueError(f"duplicate route name {route.name!r}")
            seen.add(route.name)
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def secrets(self) -> list[str]:
        """Values that must never appear in logs."""
        values = []
        if self.notify.telegram is not None:
            values.append(self.notify.telegram.bot_token)
        if self.notify.email is not None and self.notify.email.password:
            values.append(self.notify.email.password)
        return values


def load_config(path: Path | str, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror or exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: the top level must be a mapping of settings")
    raw = _expand_env(raw, env, "")
    _apply_env_overrides(raw, env)
    _merge_defaults(raw)
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_describe(exc)) from exc


def _expand_env(value: Any, env: Mapping[str, str], where: str) -> Any:
    """Replace ${NAME} in every string with the environment variable's value."""
    if isinstance(value, dict):
        return {
            key: _expand_env(item, env, f"{where}.{key}" if where else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_expand_env(item, env, f"{where}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, str):

        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in env:
                raise ConfigError(f"{where}: environment variable {name} is not set")
            return env[name]

        return _ENV_REF.sub(substitute, value)
    return value


def _apply_env_overrides(raw: dict[str, Any], env: Mapping[str, str]) -> None:
    if env.get("CFR_SCHEDULE"):
        raw["schedule"] = env["CFR_SCHEDULE"]
    if env.get("CFR_LOG_LEVEL"):
        section = raw.get("logging")
        raw["logging"] = {
            **(section if isinstance(section, dict) else {}),
            "level": env["CFR_LOG_LEVEL"],
        }


def _merge_defaults(raw: dict[str, Any]) -> None:
    """Copy `defaults` into every route; a key set on the route replaces the default entirely."""
    defaults = raw.pop("defaults", None) or {}
    if not isinstance(defaults, dict):
        raise ConfigError("defaults: must be a mapping of route settings")
    routes = raw.get("routes")
    if not isinstance(routes, list):
        return
    merged = []
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            merged.append(route)
            continue
        trip = route.get("trip", defaults.get("trip", "round-trip"))
        if trip == "one-way" and "nights" in route:
            raise ConfigError(
                f"routes[{index}].nights: only used for round trips; "
                "remove it or set trip: round-trip"
            )
        merged.append({**defaults, **route})
    raw["routes"] = merged


def _describe(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        where = "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error["loc"]
        ).lstrip(".")
        message = error["msg"].removeprefix("Value error, ")
        lines.append(f"{where or 'config'}: {message}")
    return "\n".join(lines)
