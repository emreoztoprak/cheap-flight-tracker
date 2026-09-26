"""Command-line entry point.

Default: the scheduler plus the web dashboard. --no-ui: scheduler only.
--once / --dry-run / --test-notify: one action, then exit.
"""

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
from typing import Any

import uvicorn

from .app import App
from .config import Config, ConfigError
from .coordinator import Coordinator
from .google import GoogleFlights, make_http_client
from .logging_setup import LogBuffer, configure_logging
from .messages import check_message
from .notify.base import ConsoleNotifier, Dispatcher, Notifier
from .notify.factory import build_notifiers
from .scheduler import run_forever, touch_heartbeat
from .settings_io import SettingsFiles
from .store import Store

log = logging.getLogger("cheap_flights")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cheap-flights", description="Watch Google Flights prices and send price reports."
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
        "--env-file",
        default=os.environ.get("CFR_ENV_FILE"),
        help="secrets file (default: $CFR_ENV_FILE or .env next to the config file)",
    )
    parser.add_argument(
        "--log-level", type=str.upper, choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    parser.add_argument(
        "--no-ui", action="store_true", help="run the scheduler without the dashboard"
    )
    parser.add_argument(
        "--ui-host",
        default=os.environ.get("CFR_UI_HOST", "127.0.0.1"),
        help="dashboard address (default: $CFR_UI_HOST or 127.0.0.1; the image sets 0.0.0.0)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=int(os.environ.get("CFR_UI_PORT", "8080")),
        help="dashboard port (default: $CFR_UI_PORT or 8080)",
    )
    return parser.parse_args(argv)


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
    if threading.current_thread() is not threading.main_thread():
        return
    handler = _make_signal_handler(stop)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _no_progress() -> None:
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    buffer = LogBuffer()
    configure_logging(args.log_level or "INFO", "text", buffer=buffer)
    files = SettingsFiles(args.config, args.env_file)
    data_dir = Path(args.data_dir)
    one_shot = args.once or args.dry_run or args.test_notify

    def apply_logging(config: Config) -> None:
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
            buffer=buffer,
        )

    if one_shot or args.no_ui:
        try:
            loaded = files.load()
        except ConfigError as exc:
            log.error("configuration error in %s:\n%s", args.config, exc)
            return 2
        apply_logging(loaded.config)
        if args.test_notify:
            return send_test(build_notifiers(loaded.config))
        if args.dry_run:
            return _run_once(loaded, Store(":memory:"), [ConsoleNotifier()], None, _no_progress)

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        store = Store(data_dir / "state.db")
    except (OSError, RuntimeError) as exc:
        log.error("cannot use data directory %s: %s", data_dir, exc)
        return 2
    heartbeat = data_dir / "heartbeat"
    progress = functools.partial(touch_heartbeat, heartbeat)
    try:
        if args.once:
            return _run_once(loaded, store, build_notifiers(loaded.config), data_dir, progress)
        return _serve(args, files, store, buffer, apply_logging, data_dir, heartbeat, progress)
    finally:
        store.close()


def _fetcher_factory(debug_dir: Path | None) -> Callable[[Config], GoogleFlights]:
    def make(config: Config) -> GoogleFlights:
        return GoogleFlights(make_http_client(config.search.timeout_seconds), debug_dir=debug_dir)

    return make


def _run_once(
    loaded: Any,
    store: Store,
    notifiers: list[Notifier],
    data_dir: Path | None,
    progress: Callable[[], None],
) -> int:
    stop = threading.Event()
    _handle_signals(stop)
    fetcher = _fetcher_factory(data_dir / "debug" if data_dir else None)(loaded.config)
    app = App(
        loaded.config,
        loaded.routes,
        fetcher,
        store,
        Dispatcher(notifiers),
        should_stop=stop.is_set,
        on_progress=progress,
        trigger="manual",
    )
    return 1 if app.run_once().is_failed() else 0


def _serve(
    args: argparse.Namespace,
    files: SettingsFiles,
    store: Store,
    buffer: LogBuffer,
    apply_logging: Callable[[Config], None],
    data_dir: Path,
    heartbeat: Path,
    progress: Callable[[], None],
) -> int:
    stop = threading.Event()
    _handle_signals(stop)
    coordinator = Coordinator(
        files,
        store,
        fetcher_factory=_fetcher_factory(data_dir / "debug"),
        on_config=apply_logging,
        should_stop=stop.is_set,
        on_progress=progress,
    )
    coordinator.reload()
    if coordinator.loaded is not None:
        config = coordinator.loaded.config
        log.info(
            "scheduler started: schedule %r in %s, %d route(s)",
            config.schedule,
            config.timezone,
            len(config.routes),
        )

    server = None
    server_thread = None
    if not args.no_ui:
        from .web.app import create_app

        server = uvicorn.Server(
            uvicorn.Config(
                create_app(coordinator, buffer),
                host=args.ui_host,
                port=args.ui_port,
                log_config=None,
                access_log=False,
            )
        )
        server_thread = threading.Thread(target=server.run, name="dashboard", daemon=True)
        server_thread.start()
        log.info("dashboard on port %d (http://localhost:%d)", args.ui_port, args.ui_port)

    try:
        run_forever(lambda: coordinator.run("scheduled"), coordinator.schedule, stop, heartbeat)
    finally:
        # A "Run now" stops at its next search (it watches the same stop flag); let it finish
        # recording before the web server and the database go away.
        if not coordinator.wait_idle(60):
            log.warning("a run is still active; stopping anyway")
        if server is not None:
            server.should_exit = True
            if server_thread is not None:
                server_thread.join(10)
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
