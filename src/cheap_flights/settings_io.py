"""Read, validate and write config.yaml and its .env file (used by the web UI and the CLI).

Secrets live in .env and are referenced from config.yaml as ${NAME}. Values in .env override
the process environment, so a container can still be configured with plain env variables.
"""

from __future__ import annotations

import functools
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Config, ConfigError, load_config
from .envfile import parse_env, update_env
from .places import PlaceIndex, ResolvedRoute, resolve_routes


@dataclass(frozen=True)
class FieldError:
    path: str  # e.g. "routes[0].nights", "routes.r1", or "" for the whole file
    message: str


@dataclass(frozen=True)
class Loaded:
    config: Config
    routes: list[ResolvedRoute]


@functools.cache
def place_index() -> PlaceIndex:
    return PlaceIndex.bundled()


def field_errors(exc: ConfigError) -> list[FieldError]:
    errors = []
    for line in str(exc).splitlines():
        path, sep, message = line.partition(": ")
        errors.append(
            FieldError(path, message) if sep and " " not in path else FieldError("", line)
        )
    return errors


def dump_yaml(raw: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(raw), sort_keys=False, allow_unicode=True, default_flow_style=False)


def _write_atomic(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class SettingsFiles:
    def __init__(
        self,
        config_path: Path | str,
        env_path: Path | str | None = None,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.env_path = Path(env_path) if env_path else self.config_path.with_name(".env")
        self._base_env = os.environ if base_env is None else base_env

    def exists(self) -> bool:
        return self.config_path.exists()

    def raw_text(self) -> str:
        return self.config_path.read_text(encoding="utf-8") if self.exists() else ""

    def raw(self) -> dict[str, Any]:
        try:
            data = yaml.safe_load(self.raw_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{self.config_path} is not valid YAML: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def env_text(self) -> str:
        return self.env_path.read_text(encoding="utf-8") if self.env_path.exists() else ""

    def env_file(self) -> dict[str, str]:
        return parse_env(self.env_text())

    def environment(self, env_file: Mapping[str, str] | None = None) -> dict[str, str]:
        return {**self._base_env, **(self.env_file() if env_file is None else env_file)}

    def load(self) -> Loaded:
        if not self.exists():
            raise ConfigError(f"no configuration yet ({self.config_path} does not exist)")
        return self._validate(self.raw_text(), self.env_file())

    def save(
        self, raw: Mapping[str, Any], env_changes: Mapping[str, str | None]
    ) -> list[FieldError]:
        """Validate and write both files; returns the problems, or [] when saved."""
        env_text = update_env(self.env_text(), env_changes)
        return self._save(dump_yaml(raw), env_text)

    def save_text(self, config_text: str) -> list[FieldError]:
        try:
            yaml.safe_load(config_text)
        except yaml.YAMLError as exc:
            return [FieldError("", f"not valid YAML: {exc}")]
        return self._save(config_text, self.env_text())

    def _save(self, config_text: str, env_text: str) -> list[FieldError]:
        try:
            self._validate(config_text, parse_env(env_text))
        except ConfigError as exc:
            return field_errors(exc)
        if env_text != self.env_text():
            _write_atomic(self.env_path, env_text, mode=0o600)  # first: config may refer to it
        _write_atomic(self.config_path, config_text, mode=0o644)
        return []

    def _validate(self, config_text: str, env_file: Mapping[str, str]) -> Loaded:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(config_text, encoding="utf-8")
            config = load_config(path, self.environment(env_file))
        return Loaded(config, resolve_routes(config.routes, place_index()))
