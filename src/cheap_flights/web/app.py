"""The web dashboard: settings editor, run control, status and history."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ..config import WEEKDAYS, ConfigError, EmailConfig, Route
from ..coordinator import Coordinator
from ..logging_setup import LogBuffer
from ..messages import check_message
from ..notify.base import Notifier
from ..notify.email import EmailNotifier
from ..notify.telegram import TelegramNotifier
from ..places import ResolvedRoute
from ..planner import build_plan
from ..settings_io import FieldError, place_index
from . import forms, views

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

MakeNotifier = Callable[[str, Mapping[str, Any]], Notifier]
SECRET_FIELDS = ("telegram_bot_token", "smtp_password")

NOTIFY_ERROR_FIELDS = {
    "notify.telegram.bot_token": "telegram_bot_token",
    "notify.telegram.chat_id": "telegram_chat_id",
    "notify.email.smtp_host": "smtp_host",
    "notify.email.smtp_port": "smtp_port",
    "notify.email.username": "smtp_username",
    "notify.email.password": "smtp_password",
    "notify.email.from": "email_from",
    "notify.email.to": "email_to",
    "notify": "telegram_enabled",
}


def default_make_notifier(channel: str, settings: Mapping[str, Any]) -> Notifier:
    if channel == "telegram":
        return TelegramNotifier(settings["bot_token"], settings["chat_id"])
    return EmailNotifier(EmailConfig.model_validate(dict(settings)))


def _utc_now() -> datetime:
    return datetime.now(UTC)


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def default_allowed_hosts() -> set[str]:
    extra = os.environ.get("CFR_UI_ALLOWED_HOSTS", "")
    return set(LOCAL_HOSTS) | {h.strip().lower() for h in extra.split(",") if h.strip()}


def _hostname(netloc: str) -> str:
    return (urlsplit(f"//{netloc}").hostname or "").lower()


def create_app(
    coordinator: Coordinator,
    log_buffer: LogBuffer,
    *,
    make_notifier: MakeNotifier = default_make_notifier,
    clock: Callable[[], datetime] = _utc_now,
    allowed_hosts: Iterable[str] | None = None,
) -> FastAPI:
    hosts = {h.lower().strip("[]") for h in (allowed_hosts or default_allowed_hosts())}
    app = FastAPI(title="Cheap Flight Tracker", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    files = coordinator.files
    masked: dict[str, list[str]] = {"secrets": []}

    def mask(value: Any) -> Any:
        # Last line of defence: no rendered value may contain a secret, however it got there.
        if isinstance(value, str):
            for secret in masked["secrets"]:
                if secret in value:
                    value = value.replace(secret, "***")
        return value

    templates.env.finalize = mask

    @app.middleware("http")
    async def local_and_same_origin_only(request: Request, call_next):
        # There is no login, so: answer only to known host names (blocks DNS rebinding), and
        # refuse state-changing requests that another website makes from your browser.
        host = request.headers.get("host", "")
        if _hostname(host) not in hosts:
            return PlainTextResponse(
                "Host not allowed. Add it to CFR_UI_ALLOWED_HOSTS to use this address.",
                status_code=400,
            )
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin is not None:
                same = urlsplit(origin).netloc == host
            else:
                same = request.headers.get("sec-fetch-site") in ("same-origin", "none")
            if not same:
                return PlainTextResponse("cross-site request refused", status_code=403)
        return await call_next(request)

    def page(request: Request, name: str, active: str, status_code: int = 200, **context: Any):
        masked["secrets"] = coordinator.secrets()
        now = clock()
        return templates.TemplateResponse(
            request,
            name,
            {
                "active": active,
                "config_error": coordinator.error if coordinator.loaded or files.exists() else None,
                "saved": request.query_params.get("saved"),
                "status": views.status(coordinator, now),
                "fmt": views.fmt,
                "run_summary": views.run_summary,
                "duration": views.duration,
                "weekdays": WEEKDAYS,
                **context,
            },
            status_code=status_code,
        )

    def raw_config() -> dict[str, Any]:
        try:
            return files.raw()
        except ConfigError:
            return {}

    def redirect(path: str) -> RedirectResponse:
        return RedirectResponse(f"{path}?saved=1", status_code=303)

    # --- dashboard ------------------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        if coordinator.loaded is None and not files.exists():
            return page(request, "welcome.html", "dashboard")
        return page(
            request, "dashboard.html", "dashboard", cards=views.route_cards(coordinator, clock())
        )

    @app.get("/partials/status", response_class=HTMLResponse)
    def status_partial(request: Request):
        return page(request, "_status.html", "dashboard")

    @app.post("/run", response_class=HTMLResponse)
    def run_now(request: Request):
        result = coordinator.start_run("manual")
        notes = {
            "started": "Check started.",
            "busy": "A check is already running.",
            "no_config": "Save a valid configuration first.",
        }
        return page(request, "_status.html", "dashboard", note=notes[result])

    # --- setup ----------------------------------------------------------------------------

    @app.get("/setup", response_class=HTMLResponse)
    def setup_form(request: Request):
        if files.exists():
            return RedirectResponse("/", status_code=303)
        return page(
            request,
            "setup.html",
            "dashboard",
            route=_blank_route(),
            notify=forms.notify_to_form(None),
            field_errors={},
            general_errors=[],
        )

    @app.post("/setup", response_class=HTMLResponse)
    async def setup_save(request: Request):
        if files.exists():
            return RedirectResponse("/", status_code=303)  # never overwrite an existing config
        form = await request.form()
        route = forms.route_from_form(form, {})
        notify, env, notify_errors = forms.notify_from_form(form, None)
        notify_errors = forms.env_references(form, SECRET_FIELDS) + notify_errors
        errors = notify_errors or coordinator.save({"routes": [route], "notify": notify}, env)
        if not errors:
            return redirect("/")
        fields, general = forms.route_errors(errors, 0, route["name"])
        fields.update(_notify_fields(errors))
        return page(
            request,
            "setup.html",
            "dashboard",
            status_code=200,
            route=_submitted(form),
            notify=_submitted_notify(form),
            field_errors=fields,
            general_errors=[g for g in general if not g.startswith(("notify", "telegram", "smtp"))],
        )

    # --- routes ---------------------------------------------------------------------------

    @app.get("/routes", response_class=HTMLResponse)
    def routes_list(request: Request, error: str | None = None):
        raw = raw_config()
        defaults = raw.get("defaults") or {}
        routes = [forms.route_to_form(r, defaults) for r in raw.get("routes") or []]
        return page(request, "routes.html", "routes", routes=routes, error=error)

    @app.get("/routes/new", response_class=HTMLResponse)
    def route_new(request: Request):
        raw = raw_config()
        route = forms.route_to_form(
            {"name": "", "from": "", "to": [], "alert": {}}, raw.get("defaults") or {}
        )
        return page(
            request,
            "route_form.html",
            "routes",
            route={**_blank_route(), **route, "name": ""},
            index=None,
            field_errors={},
            general_errors=[],
        )

    @app.post("/routes/estimate", response_class=HTMLResponse)
    async def route_estimate(request: Request):
        form = await request.form()
        defaults = raw_config().get("defaults") or {}
        route_raw = forms.route_from_form(form, defaults)
        probe = {**defaults, **route_raw, "name": "estimate", "alert": {"max_price": 1}}
        try:
            route = Route.model_validate(probe)
            origins = place_index().resolve_origin(route.origin)
            destinations = [
                p
                for text in route.to
                for p in place_index().resolve_destination(text, route.max_airports_per_country)
            ]
        except (ValidationError, ConfigError):
            return HTMLResponse(
                '<span class="muted">Fill in from, to and dates to see the search count.</span>'
            )
        today = clock().astimezone(ZoneInfo("UTC")).date()
        plan = build_plan([ResolvedRoute(route, tuple(origins), tuple(destinations))], today, 10**9)
        return HTMLResponse(f"≈ <strong>{plan.requested}</strong> searches per run")

    @app.get("/routes/{index}", response_class=HTMLResponse)
    def route_edit(request: Request, index: int):
        raw = raw_config()
        routes = raw.get("routes") or []
        if not 0 <= index < len(routes):
            return RedirectResponse("/routes", status_code=303)
        route = forms.route_to_form(routes[index], raw.get("defaults") or {})
        return page(
            request,
            "route_form.html",
            "routes",
            route={**_blank_route(), **route},
            index=index,
            field_errors={},
            general_errors=[],
        )

    async def save_route(request: Request, index: int | None):
        form = await request.form()
        refs = forms.env_references(form)
        if refs:
            return page(
                request,
                "route_form.html",
                "routes",
                route=_submitted(form),
                index=index,
                field_errors={},
                general_errors=[f"{e.path}: {e.message}" for e in refs],
            )
        with coordinator.edit_lock:
            return _save_route(request, form, index)

    def _save_route(request: Request, form: Any, index: int | None):
        raw = raw_config()
        routes = list(raw.get("routes") or [])
        route = forms.route_from_form(form, raw.get("defaults") or {})
        if index is None:
            routes.append(route)
            position = len(routes) - 1
        elif 0 <= index < len(routes):
            routes[index] = route
            position = index
        else:
            return RedirectResponse("/routes", status_code=303)
        errors = coordinator.save({**raw, "routes": routes}, {})
        if not errors:
            return redirect("/routes")
        fields, general = forms.route_errors(errors, position, route["name"])
        return page(
            request,
            "route_form.html",
            "routes",
            route=_submitted(form),
            index=index,
            field_errors=fields,
            general_errors=general,
        )

    @app.post("/routes/new", response_class=HTMLResponse)
    async def route_create(request: Request):
        return await save_route(request, None)

    @app.post("/routes/{index}", response_class=HTMLResponse)
    async def route_update(request: Request, index: int):
        return await save_route(request, index)

    @app.post("/routes/{index}/delete", response_class=HTMLResponse)
    def route_delete(request: Request, index: int):
        with coordinator.edit_lock:
            return _route_delete(request, index)

    def _route_delete(request: Request, index: int):
        raw = raw_config()
        routes = list(raw.get("routes") or [])
        if len(routes) <= 1:
            return routes_list(
                request, error="Keep at least one route — edit it instead of deleting it."
            )
        if 0 <= index < len(routes):
            del routes[index]
            errors = coordinator.save({**raw, "routes": routes}, {})
            if errors:
                return routes_list(request, error="; ".join(e.message for e in errors))
        return redirect("/routes")

    @app.post("/routes/{index}/duplicate", response_class=HTMLResponse)
    def route_duplicate(request: Request, index: int):
        with coordinator.edit_lock:
            return _route_duplicate(request, index)

    def _route_duplicate(request: Request, index: int):
        raw = raw_config()
        routes = list(raw.get("routes") or [])
        if 0 <= index < len(routes):
            names = {str(r.get("name")) for r in routes}
            copy = dict(routes[index])
            base, number = f"{copy.get('name', 'route')}-copy", 1
            copy["name"] = base
            while copy["name"] in names:
                number += 1
                copy["name"] = f"{base}{number}"
            routes.insert(index + 1, copy)
            errors = coordinator.save({**raw, "routes": routes}, {})
            if errors:
                return routes_list(request, error="; ".join(e.message for e in errors))
        return redirect("/routes")

    @app.get("/places", response_class=HTMLResponse)
    def places(request: Request, target: str = "to"):
        # htmx sends the field's own value (?to=... or ?origin=...); "to" is a comma list.
        text = request.query_params.get(target) or request.query_params.get("q") or ""
        query = text.split(",")[-1].strip() if target == "to" else text.strip()
        return templates.TemplateResponse(
            request, "_places.html", {"options": place_index().suggest(query), "target": target}
        )

    # --- notifications --------------------------------------------------------------------

    @app.get("/notifications", response_class=HTMLResponse)
    def notifications(request: Request):
        config = coordinator.loaded.config if coordinator.loaded else None
        return page(
            request,
            "notifications.html",
            "notifications",
            notify=forms.notify_to_form(config),
            field_errors={},
            general_errors=[],
        )

    @app.post("/notifications", response_class=HTMLResponse)
    async def notifications_save(request: Request):
        form = await request.form()
        config = coordinator.loaded.config if coordinator.loaded else None
        notify, env, errors = forms.notify_from_form(form, config)
        errors = forms.env_references(form, SECRET_FIELDS) + errors
        if not errors:
            with coordinator.edit_lock:
                raw = raw_config()
                if not raw.get("routes"):
                    errors = [
                        FieldError(
                            "", "Add a route first (Setup) — a config needs at least one route."
                        )
                    ]
                else:
                    errors = coordinator.save({**raw, "notify": notify}, env)
        if not errors:
            return redirect("/notifications")
        fields = _notify_fields(errors)
        general = [
            e.message for e in errors if not (e.path in NOTIFY_ERROR_FIELDS or e.path in fields)
        ]
        return page(
            request,
            "notifications.html",
            "notifications",
            notify={**forms.notify_to_form(config), **_submitted_notify(form)},
            field_errors=fields,
            general_errors=general,
        )

    @app.post("/notifications/test/{channel}", response_class=HTMLResponse)
    async def notifications_test(request: Request, channel: str):
        form = await request.form()
        config = coordinator.loaded.config if coordinator.loaded else None
        try:
            settings = _channel_settings(channel, form, config)
            notifier = make_notifier(channel, settings)
            await run_in_threadpool(notifier.send, check_message())  # SMTP can take seconds
        except Exception as exc:
            return HTMLResponse(f'<span class="result bad">Failed: {_escape(str(exc))}</span>')
        return HTMLResponse('<span class="result good">Sent ✓ — check your messages</span>')

    # --- settings -------------------------------------------------------------------------

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        return page(
            request,
            "settings.html",
            "settings",
            values=forms.settings_to_form(raw_config()),
            raw_text=files.raw_text(),
            field_errors={},
            general_errors=[],
            raw_errors=[],
        )

    @app.post("/settings", response_class=HTMLResponse)
    async def settings_save(request: Request):
        form = await request.form()
        errors = forms.env_references(form)
        with coordinator.edit_lock:
            raw = raw_config()
            if errors:
                pass
            elif not raw.get("routes"):
                errors = [
                    FieldError(
                        "",
                        "Finish Setup first — a config needs a route and a notification channel.",
                    )
                ]
            else:
                errors = coordinator.save(forms.settings_from_form(form, raw), {})
        if not errors:
            return redirect("/settings")
        known = {field for field, *_ in forms.SETTINGS_FIELDS}
        fields = {e.path: e.message for e in errors if e.path in known}
        general = [
            f"{e.path}: {e.message}" if e.path else e.message
            for e in errors
            if e.path not in fields
        ]
        values = {key: str(form.get(key) or "") for key in forms.settings_to_form(raw)}
        return page(
            request,
            "settings.html",
            "settings",
            values=values,
            raw_text=files.raw_text(),
            field_errors=fields,
            general_errors=general,
            raw_errors=[],
        )

    @app.post("/settings/raw", response_class=HTMLResponse)
    async def settings_raw(request: Request):
        form = await request.form()
        text = str(form.get("text") or "")
        errors = coordinator.save_text(text)
        if not errors:
            return redirect("/settings")
        return page(
            request,
            "settings.html",
            "settings",
            values=forms.settings_to_form(raw_config()),
            raw_text=text,
            field_errors={},
            general_errors=[],
            raw_errors=[f"{e.path}: {e.message}" if e.path else e.message for e in errors],
        )

    @app.get("/settings/cron", response_class=HTMLResponse)
    def cron_preview(schedule: str = "", timezone: str = "UTC"):
        if not croniter.is_valid(schedule):
            return HTMLResponse('<span class="result bad">not a valid cron expression</span>')
        try:
            tz = ZoneInfo(timezone or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            return HTMLResponse('<span class="result bad">unknown time zone</span>')
        it = croniter(schedule, clock().astimezone(tz))
        upcoming = ", ".join(it.get_next(datetime).strftime("%a %d %b %H:%M") for _ in range(3))
        return HTMLResponse(f'<span class="muted">Next runs: {upcoming}</span>')

    # --- history --------------------------------------------------------------------------

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, route: str | None = None):
        names = [r.route.name for r in coordinator.loaded.routes] if coordinator.loaded else []
        tz = views.tz_of(coordinator)
        alerts = coordinator.store.alerts(100)
        runs = coordinator.store.recent_runs(20)
        return page(
            request,
            "history.html",
            "history",
            names=names,
            selected=route or (names[0] if names else ""),
            alerts=alerts,
            runs=runs,
            tz=tz,
        )

    @app.get("/history/data/{route}")
    def history_data(route: str, days: int = 90):
        return JSONResponse(
            views.history_series(coordinator, route, clock() - timedelta(days=days))
        )

    @app.get("/partials/logs", response_class=HTMLResponse)
    def logs(request: Request, level: str = "INFO"):
        entries = list(reversed(log_buffer.entries(min_level=level, limit=200)))
        return templates.TemplateResponse(request, "_logs.html", {"entries": entries})

    return app


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _blank_route() -> dict[str, Any]:
    return {
        "name": "",
        "origin": "",
        "to": "",
        "trip": "round-trip",
        "nights_min": "3",
        "nights_max": "7",
        "window_mode": "next",
        "next_days": "90",
        "date_from": "",
        "date_to": "",
        "weekdays": [],
        "depart_from": "",
        "depart_to": "",
        "stops": "any",
        "currency": "EUR",
        "adults": "1",
        "children": "0",
        "top_n": "3",
        "max_airports_per_country": "10",
        "max_price": "",
        "drop_percent": "",
    }


def _submitted(form: Any) -> dict[str, Any]:
    values = _blank_route()
    for key in values:
        if key == "weekdays":
            values[key] = list(form.getlist("weekdays"))
        elif form.get(key) is not None:
            values[key] = str(form.get(key))
    return values


def _submitted_notify(form: Any) -> dict[str, Any]:
    keys = ("telegram_chat_id", "smtp_host", "smtp_port", "smtp_username", "email_from", "email_to")
    values: dict[str, Any] = {key: str(form.get(key) or "") for key in keys}
    values["telegram_enabled"] = bool(form.get("telegram_enabled"))
    values["email_enabled"] = bool(form.get("email_enabled"))
    return values


def _notify_fields(errors: list[FieldError]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for error in errors:
        name = NOTIFY_ERROR_FIELDS.get(error.path) or (
            error.path if error.path in NOTIFY_ERROR_FIELDS.values() else None
        )
        if name:
            fields.setdefault(name, error.message)
    return fields


def _channel_settings(channel: str, form: Any, config: Any) -> dict[str, Any]:
    text = lambda key: str(form.get(key) or "").strip()  # noqa: E731
    if channel == "telegram":
        current = config.notify.telegram if config else None
        token = text("telegram_bot_token") or (current.bot_token if current else "")
        if not token:
            raise ValueError("enter the bot token")
        return {"bot_token": token, "chat_id": text("telegram_chat_id")}
    if channel == "email":
        current = config.notify.email if config else None
        settings: dict[str, Any] = {
            "smtp_host": text("smtp_host"),
            "smtp_port": int(text("smtp_port") or 587),
            "from": text("email_from"),
            "to": [a.strip() for a in text("email_to").split(",") if a.strip()],
        }
        if text("smtp_username"):
            settings["username"] = text("smtp_username")
            settings["password"] = text("smtp_password") or forms.saved_smtp_password(
                current, settings["smtp_host"], settings["username"]
            )
            if not settings["password"]:
                raise ValueError(
                    "enter the password (the saved one only works for the saved server)"
                )
        return settings
    raise ValueError(f"unknown channel {channel!r}")
