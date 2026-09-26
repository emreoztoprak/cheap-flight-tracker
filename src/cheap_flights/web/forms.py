"""Translate between HTML form fields and the config.yaml / .env structure.

Route forms show a route's *effective* values (defaults applied) and save back only the values
that differ from the defaults, so routes keep inheriting later changes to `defaults`.
Secrets never go into form values; a blank secret field means "keep the current one".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from ..config import WEEKDAYS, Config, Route
from ..settings_io import FieldError

TELEGRAM_TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
SMTP_PASSWORD_VAR = "SMTP_PASSWORD"


class FormLike(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def getlist(self, key: str) -> list[Any]: ...


def _text(form: FormLike, key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


def _number(value: str, kind: type) -> Any:
    """Convert when possible; otherwise keep the text so validation reports the field."""
    try:
        return kind(value)
    except ValueError:
        return value


def _plain(value: float | int) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]


# --- routes -------------------------------------------------------------------------------

_COMPACTABLE = (
    "trip",
    "nights",
    "window",
    "stops",
    "currency",
    "passengers",
    "top_n",
    "max_airports_per_country",
)

ROUTE_ERROR_FIELDS = {
    "name": "name",
    "from": "origin",
    "to": "to",
    "trip": "trip",
    "nights": "nights_min",
    "window": "next_days",
    "weekdays": "weekdays",
    "depart_time": "depart_from",
    "stops": "stops",
    "currency": "currency",
    "passengers": "adults",
    "top_n": "top_n",
    "max_airports_per_country": "max_airports_per_country",
    "alert": "max_price",
    "max_price": "max_price",
    "drop_percent": "drop_percent",
}


def route_to_form(raw_route: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    merged = {**defaults, **raw_route}
    try:
        route = Route.model_validate(merged)
    except ValidationError:
        to = merged.get("to", [])
        return {
            "name": str(merged.get("name", "")),
            "origin": str(merged.get("from", "")),
            "to": ", ".join(to) if isinstance(to, list) else str(to),
        }
    window = route.window
    return {
        "name": route.name,
        "origin": route.origin,
        "to": ", ".join(route.to),
        "trip": route.trip,
        "nights_min": str(route.nights[0]),
        "nights_max": str(route.nights[1]),
        "window_mode": "next" if window.next_days is not None else "range",
        "next_days": str(window.next_days or ""),
        "date_from": window.start.isoformat() if window.start else "",
        "date_to": window.end.isoformat() if window.end else "",
        "weekdays": [WEEKDAYS[day] for day in route.weekdays or ()],
        "depart_from": str(route.depart_time[0]) if route.depart_time else "",
        "depart_to": str(route.depart_time[1]) if route.depart_time else "",
        "stops": route.stops,
        "currency": route.currency,
        "adults": str(route.passengers.adults),
        "children": str(route.passengers.children),
        "top_n": str(route.top_n),
        "max_airports_per_country": str(route.max_airports_per_country),
        "max_price": str(route.alert.max_price) if route.alert.max_price is not None else "",
        "drop_percent": _plain(route.alert.drop_percent)
        if route.alert.drop_percent is not None
        else "",
    }


def route_from_form(form: FormLike, defaults: Mapping[str, Any]) -> dict[str, Any]:
    full: dict[str, Any] = {
        "name": _text(form, "name"),
        "from": _text(form, "origin"),
        "to": _split(_text(form, "to")),
    }
    trip = _text(form, "trip") or "round-trip"
    full["trip"] = trip
    if trip == "round-trip":
        low, high = (
            _text(form, "nights_min"),
            _text(form, "nights_max") or _text(form, "nights_min"),
        )
        full["nights"] = low if low == high else f"{low}-{high}"
    if _text(form, "window_mode") == "range":
        full["window"] = {"from": _text(form, "date_from"), "to": _text(form, "date_to")}
    else:
        full["window"] = {"next_days": _number(_text(form, "next_days"), int)}
    weekdays = [day for day in WEEKDAYS if day in form.getlist("weekdays")]
    if weekdays and len(weekdays) < len(WEEKDAYS):
        full["weekdays"] = weekdays
    start, end = _text(form, "depart_from"), _text(form, "depart_to")
    if start or end:
        start, end = start or "0", end or "23"
        if start.isdigit() and end.isdigit():
            full["depart_time"] = f"{int(start):02d}:00-{int(end):02d}:00"
        else:
            full["depart_time"] = f"{start}-{end}"  # rejected by validation with a clear message
    full["stops"] = _text(form, "stops") or "any"
    full["currency"] = _text(form, "currency").upper() or "EUR"
    passengers: dict[str, Any] = {"adults": _number(_text(form, "adults") or "1", int)}
    children = _number(_text(form, "children") or "0", int)
    if children:
        passengers["children"] = children
    full["passengers"] = passengers
    full["top_n"] = _number(_text(form, "top_n") or "3", int)
    full["max_airports_per_country"] = _number(_text(form, "max_airports_per_country") or "10", int)
    alert: dict[str, Any] = {}
    if _text(form, "max_price"):
        alert["max_price"] = _number(_text(form, "max_price"), int)
    if _text(form, "drop_percent"):
        drop = _number(_text(form, "drop_percent"), float)
        alert["drop_percent"] = int(drop) if isinstance(drop, float) and drop.is_integer() else drop
    full["alert"] = alert
    return _compact(full, defaults)


def _compact(full: dict[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    stub = {
        "name": "probe",
        "from": full["from"] or "IST",
        "to": full["to"] or ["LHR"],
        "alert": {"max_price": 1},
    }
    try:
        base = Route.model_validate({**defaults, **stub})
    except ValidationError:
        return full
    out = dict(full)
    for key in _COMPACTABLE:
        if key not in out:
            continue
        try:
            candidate = Route.model_validate({**defaults, **stub, key: out[key]})
        except ValidationError:
            continue
        if getattr(candidate, key) == getattr(base, key):
            del out[key]
    return out


def route_errors(
    errors: list[FieldError], index: int, name: str
) -> tuple[dict[str, str], list[str]]:
    """Split save errors into {form field: message} for this route and general messages."""
    fields: dict[str, str] = {}
    general: list[str] = []
    prefixes = (f"routes[{index}].", f"routes.{name}")
    for error in errors:
        rest = next((error.path[len(p) :] for p in prefixes if error.path.startswith(p)), None)
        if rest is None:
            general.append(f"{error.path}: {error.message}" if error.path else error.message)
            continue
        parts = [p for p in rest.split(".") if p]
        key = (
            parts[-1] if parts and parts[0] == "alert" and len(parts) > 1 else (parts or ["to"])[0]
        )
        fields.setdefault(ROUTE_ERROR_FIELDS.get(key, "to"), error.message)
    return fields, general


# --- notifications ------------------------------------------------------------------------


def notify_to_form(config: Config | None) -> dict[str, Any]:
    telegram = config.notify.telegram if config else None
    email = config.notify.email if config else None
    return {
        "telegram_enabled": telegram is not None,
        "telegram_chat_id": telegram.chat_id if telegram else "",
        "telegram_has_token": telegram is not None,
        "email_enabled": email is not None,
        "smtp_host": email.smtp_host if email else "",
        "smtp_port": str(email.smtp_port) if email else "587",
        "smtp_username": (email.username or "") if email else "",
        "email_has_password": bool(email and email.password),
        "email_from": email.sender if email else "",
        "email_to": ", ".join(email.to) if email else "",
    }


def saved_smtp_password(current: Any, host: str, username: str) -> str:
    """The saved password, but only for the same server and username it was saved for."""
    if current is None or not current.password:
        return ""
    if current.smtp_host != host or (current.username or "") != username:
        return ""
    return current.password


def env_references(form: FormLike, skip: tuple[str, ...] = ()) -> list[FieldError]:
    """${NAME} would pull other secrets or environment values into visible settings."""
    errors = []
    for key in {key for key, _ in form.multi_items()} if hasattr(form, "multi_items") else []:
        if key in skip:
            continue
        for value in form.getlist(key):
            if isinstance(value, str) and "${" in value:
                errors.append(FieldError(key, "“${…}” references are not allowed here"))
                break
    return errors


def notify_from_form(
    form: FormLike, current: Config | None
) -> tuple[dict[str, Any], dict[str, str | None], list[FieldError]]:
    notify: dict[str, Any] = {}
    env: dict[str, str | None] = {}
    errors: list[FieldError] = []
    current_telegram = current.notify.telegram if current else None
    current_email = current.notify.email if current else None

    if form.get("telegram_enabled"):
        token = _text(form, "telegram_bot_token") or (
            current_telegram.bot_token if current_telegram else ""
        )
        if not token:
            errors.append(FieldError("telegram_bot_token", "enter the bot token"))
        notify["telegram"] = {
            "bot_token": f"${{{TELEGRAM_TOKEN_VAR}}}",
            "chat_id": _text(form, "telegram_chat_id"),
        }
        env[TELEGRAM_TOKEN_VAR] = token
    else:
        env[TELEGRAM_TOKEN_VAR] = None

    if form.get("email_enabled"):
        email: dict[str, Any] = {
            "smtp_host": _text(form, "smtp_host"),
            "smtp_port": _number(_text(form, "smtp_port") or "587", int),
        }
        username = _text(form, "smtp_username")
        if username:
            password = _text(form, "smtp_password") or saved_smtp_password(
                current_email, _text(form, "smtp_host"), username
            )
            if not password:
                errors.append(FieldError("smtp_password", "enter the password for this username"))
            email["username"] = username
            email["password"] = f"${{{SMTP_PASSWORD_VAR}}}"
            env[SMTP_PASSWORD_VAR] = password
        else:
            env[SMTP_PASSWORD_VAR] = None
        email["from"] = _text(form, "email_from")
        email["to"] = _split(_text(form, "email_to"))
        notify["email"] = email
    else:
        env[SMTP_PASSWORD_VAR] = None
    return notify, env, errors


# --- general settings ---------------------------------------------------------------------

# (form field, section, key, type)
SETTINGS_FIELDS: tuple[tuple[str, str | None, str, type], ...] = (
    ("schedule", None, "schedule", str),
    ("timezone", None, "timezone", str),
    ("search.delay_seconds", "search", "delay_seconds", float),
    ("search.max_searches_per_run", "search", "max_searches_per_run", int),
    ("search.retries", "search", "retries", int),
    ("search.timeout_seconds", "search", "timeout_seconds", float),
    ("health.alert_after_failed_runs", "health", "alert_after_failed_runs", int),
    ("health.reminder_hours", "health", "reminder_hours", float),
    ("logging.level", "logging", "level", str),
    ("logging.format", "logging", "format", str),
    ("logging.file", "logging", "file", str),
    ("logging.max_size_mb", "logging", "max_size_mb", float),
    ("logging.backups", "logging", "backups", int),
    ("defaults.currency", "defaults", "currency", str),
    ("defaults.trip", "defaults", "trip", str),
    ("defaults.nights", "defaults", "nights", str),
    ("defaults.stops", "defaults", "stops", str),
    ("defaults.top_n", "defaults", "top_n", int),
)


def settings_to_form(raw: Mapping[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field, section, key, _kind in SETTINGS_FIELDS:
        container = raw if section is None else raw.get(section) or {}
        value = container.get(key) if isinstance(container, Mapping) else None
        values[field] = (
            ""
            if value is None
            else _plain(value)
            if isinstance(value, int | float) and not isinstance(value, bool)
            else str(value)
        )
    if values["logging.file"] == "" and isinstance(raw.get("logging"), Mapping):
        if "file" in raw["logging"] and not raw["logging"]["file"]:
            values["logging.file"] = "none"
    defaults = raw.get("defaults") or {}
    window = defaults.get("window") if isinstance(defaults, Mapping) else None
    passengers = defaults.get("passengers") if isinstance(defaults, Mapping) else None
    values["defaults.next_days"] = (
        str(window.get("next_days", "")) if isinstance(window, Mapping) else ""
    )
    values["defaults.adults"] = (
        str(passengers.get("adults", "")) if isinstance(passengers, Mapping) else ""
    )
    return values


def settings_from_form(form: FormLike, raw: Mapping[str, Any]) -> dict[str, Any]:
    new: dict[str, Any] = {key: value for key, value in raw.items()}
    for section in ("search", "health", "logging", "defaults"):
        if isinstance(new.get(section), Mapping):
            new[section] = dict(new[section])
    for field, section, key, kind in SETTINGS_FIELDS:
        if form.get(field) is None:
            continue  # not on this form: leave as is
        text = _text(form, field)
        target = new if section is None else new.setdefault(section, {})
        if field == "logging.file" and text.lower() == "none":
            target[key] = ""
        elif text:
            target[key] = text if kind is str else _number(text, kind)
        else:
            target.pop(key, None)
    defaults = new.setdefault("defaults", {})
    if form.get("defaults.next_days") is not None:
        days = _text(form, "defaults.next_days")
        if days:
            defaults["window"] = {"next_days": _number(days, int)}
        elif isinstance(defaults.get("window"), Mapping) and "next_days" in defaults["window"]:
            defaults.pop("window")
    if form.get("defaults.adults") is not None:
        adults = _text(form, "defaults.adults")
        if adults:
            defaults["passengers"] = {
                **(defaults.get("passengers") or {}),
                "adults": _number(adults, int),
            }
        elif isinstance(defaults.get("passengers"), Mapping):
            defaults["passengers"] = {
                k: v for k, v in defaults["passengers"].items() if k != "adults"
            }
            if not defaults["passengers"]:
                defaults.pop("passengers")
    for section in ("search", "health", "logging", "defaults"):
        if section in new and not new[section]:
            new.pop(section)
    return new
