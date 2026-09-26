"""Owns the running tool: current settings, one check at a time, progress for the web UI."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from .app import App
from .config import Config, ConfigError
from .notify.base import Dispatcher, Notifier
from .notify.factory import build_notifiers
from .runner import Fetcher, RunStats
from .scheduler import Schedule
from .settings_io import FieldError, Loaded, SettingsFiles
from .store import Store

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _never() -> bool:
    return False


def _nothing() -> None:
    return None


@dataclass(frozen=True)
class Progress:
    running: bool = False
    trigger: str = ""
    started_at: datetime | None = None
    done: int = 0
    total: int = 0


class Coordinator:
    def __init__(
        self,
        files: SettingsFiles,
        store: Store,
        *,
        fetcher_factory: Callable[[Config], Fetcher],
        notifier_factory: Callable[[Config], list[Notifier]] = build_notifiers,
        on_config: Callable[[Config], None] | None = None,
        should_stop: Callable[[], bool] = _never,
        on_progress: Callable[[], None] = _nothing,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.files = files
        self.store = store
        self._fetcher_factory = fetcher_factory
        self._notifier_factory = notifier_factory
        self._on_config = on_config
        self._should_stop = should_stop
        self._on_progress = on_progress
        self._clock = clock
        self._sleep = sleep
        self._state_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._loaded: Loaded | None = None
        self._error: str | None = None
        self._progress = Progress()

    # --- settings -------------------------------------------------------------------------

    @property
    def loaded(self) -> Loaded | None:
        return self._loaded

    @property
    def error(self) -> str | None:
        """Why the files on disk can't be used right now (the last good config stays active)."""
        return self._error

    def reload(self) -> None:
        try:
            loaded = self.files.load()
        except ConfigError as exc:
            with self._state_lock:
                self._error = str(exc)
            log.error("configuration problem: %s", exc)
            return
        with self._state_lock:
            self._loaded, self._error = loaded, None
        if self._on_config is not None:
            self._on_config(loaded.config)

    def save(
        self, raw: Mapping[str, Any], env_changes: Mapping[str, str | None]
    ) -> list[FieldError]:
        errors = self.files.save(raw, env_changes)
        if not errors:
            self.reload()
            log.info("settings saved; they apply from the next run")
        return errors

    def save_text(self, text: str) -> list[FieldError]:
        errors = self.files.save_text(text)
        if not errors:
            self.reload()
            log.info("config.yaml saved; it applies from the next run")
        return errors

    def schedule(self) -> Schedule | None:
        loaded = self._loaded
        return None if loaded is None else (loaded.config.schedule, loaded.config.tz)

    def notifiers(self) -> list[Notifier]:
        loaded = self._loaded
        return [] if loaded is None else self._notifier_factory(loaded.config)

    # --- runs -----------------------------------------------------------------------------

    def progress(self) -> Progress:
        return self._progress

    def run(self, trigger: str = "scheduled") -> RunStats | None:
        """Run one check now, in this thread. None when another run is active or no config."""
        loaded = self._loaded
        if loaded is None:
            log.warning("skipping %s run: no valid configuration", trigger)
            return None
        if not self._run_lock.acquire(blocking=False):
            log.warning("skipping %s run: a run is already in progress", trigger)
            return None
        return self._run_locked(loaded, trigger)

    def start_run(self, trigger: str = "manual") -> str:
        """Start a run in the background: "started", "busy" or "no_config"."""
        loaded = self._loaded
        if loaded is None:
            return "no_config"
        if not self._run_lock.acquire(blocking=False):
            return "busy"
        thread = threading.Thread(
            target=self._run_locked, args=(loaded, trigger), name="manual-run", daemon=True
        )
        thread.start()
        return "started"

    def _run_locked(self, loaded: Loaded, trigger: str) -> RunStats:
        """Runs with self._run_lock already held; releases it."""
        try:
            self._progress = Progress(True, trigger, self._clock())
            app = App(
                loaded.config,
                loaded.routes,
                self._fetcher_factory(loaded.config),
                self.store,
                Dispatcher(self._notifier_factory(loaded.config)),
                clock=self._clock,
                sleep=self._sleep,
                should_stop=self._should_stop,
                on_progress=self._advance,
                on_planned=self._planned,
                trigger=trigger,
            )
            return app.run_once()
        finally:
            self._progress = replace(self._progress, running=False)
            self._run_lock.release()

    def wait_idle(self, timeout: float) -> bool:
        if self._run_lock.acquire(timeout=timeout):
            self._run_lock.release()
            return True
        return False

    def _planned(self, total: int) -> None:
        self._progress = replace(self._progress, total=total)

    def _advance(self) -> None:
        self._progress = replace(self._progress, done=self._progress.done + 1)
        self._on_progress()
