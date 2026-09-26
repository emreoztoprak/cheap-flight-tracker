"""Command-line entry point: scheduler (default), --once, --dry-run, --test-notify."""

from __future__ import annotations

import argparse
import functools
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from .app import App
from .config import Config, ConfigError, load_config
from .google import GoogleFlights, make_http_client
from .logging_setup import configure_logging
from .messages import check_message
from .notify.base import ConsoleNotifier, Dispatcher, Notifier
from .notify.email import EmailNotifier
from .notify.telegram import TelegramNotifier
from .places import PlaceIndex, resolve_routes
from .scheduler import run_forever, touch_heartbeat
from .store import Store

log = logging.getLogger("cheap_flights")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cheap-flights", description="Watch Google Flights prices and send deal alerts."
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("CFR_CONFIG", "/config/config.yaml"),
        help="YAML config file (default: $CFR_CONFIG or /config/config.yaml)",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("CFR_DATA_DIR", "/data"),
        help="state database, heartbeat and debug pages (default: $CFR_DATA_DIR or /data)",
    )
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run one check, print alerts instead of sending them, keep no state",
    )
    parser.add_argument(
        "--test-notify", action="store_true", help="send a test message to every channel and exit"
    )
    parser.add_argument(
        "--log-level", type=str.upper, choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args(argv)


def build_notifiers(config: Config) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if config.notify.telegram is not None:
        telegram = config.notify.telegram
        notifiers.append(TelegramNotifier(telegram.bot_token, telegram.chat_id))
    if config.notify.email is not None:
        notifiers.append(EmailNotifier(config.notify.email))
    return notifiers


def send_test(notifiers: Sequence[Notifier]) -> int:
    failures = 0
    for notifier in notifiers:
        try:
            notifier.send(check_message())
        except Exception as exc:
            failures += 1
            log.error("%s: test message failed: %s", notifier.name, exc)
        else:
            log.info("%s: test message sent", notifier.name)
    return 1 if failures else 0


def _make_signal_handler(
    stop: threading.Event, exit_now: Callable[[int], object] = os._exit
) -> Callable[[int, object], None]:
    """First signal: finish the current search and stop. Second signal: exit immediately."""

    def handler(signum: int, _frame: object) -> None:
        name = signal.Signals(signum).name
        if stop.is_set():
            log.warning("received %s again; exiting immediately", name)
            exit_now(128 + signum)
            return
        log.info("received %s; finishing the current search and stopping (repeat to force)", name)
        stop.set()

    return handler


def _handle_signals(stop: threading.Event) -> None:
    handler = _make_signal_handler(stop)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _no_progress() -> None:
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level or "INFO", "text")
    try:
        config = load_config(args.config)
        routes = resolve_routes(config.routes, PlaceIndex.bundled())
    except ConfigError as exc:
        log.error("configuration error in %s:\n%s", args.config, exc)
        return 2
    data_dir = Path(args.data_dir)
    log_file = None
    if config.logging.file and not (args.dry_run or args.test_notify):
        log_file = data_dir / config.logging.file  # an absolute path replaces data_dir
    configure_logging(
        args.log_level or config.logging.level,
        config.logging.format,
        config.secrets(),
        file=log_file,
        max_bytes=int(config.logging.max_size_mb * 1024 * 1024),
        backups=config.logging.backups,
    )

    notifiers = build_notifiers(config)
    if args.test_notify:
        return send_test(notifiers)

    heartbeat = data_dir / "heartbeat"
    if args.dry_run:
        store = Store(":memory:")
        dispatcher = Dispatcher([ConsoleNotifier()])
        progress = _no_progress
        debug_dir = None
    else:
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            store = Store(data_dir / "state.db")
        except (OSError, RuntimeError) as exc:
            log.error("cannot use data directory %s: %s", data_dir, exc)
            return 2
        dispatcher = Dispatcher(notifiers)
        progress = functools.partial(touch_heartbeat, heartbeat)
        debug_dir = data_dir / "debug"

    stop = threading.Event()
    _handle_signals(stop)
    fetcher = GoogleFlights(make_http_client(config.search.timeout_seconds), debug_dir=debug_dir)
    app = App(
        config, routes, fetcher, store, dispatcher, should_stop=stop.is_set, on_progress=progress
    )
    try:
        if args.once or args.dry_run:
            return 1 if app.run_once().is_failed() else 0
        log.info(
            "scheduler started: schedule %r in %s, %d route(s)",
            config.schedule,
            config.timezone,
            len(routes),
        )
        run_forever(app.run_once, config.schedule, config.tz, stop, heartbeat)
        log.info("stopped")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
