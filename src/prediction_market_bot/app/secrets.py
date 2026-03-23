from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from prediction_market_bot.app.settings import AppSettings


class SecretProvider(Protocol):
    def get(self, key: str, *, default: str = "", required: bool = False) -> str:
        ...


class SecretResolutionError(ValueError):
    """Raised when a required secret cannot be resolved."""


@dataclass(slots=True)
class EnvSecretProvider:
    def get(self, key: str, *, default: str = "", required: bool = False) -> str:
        resolved_key = key.strip()
        if not resolved_key:
            if required:
                raise SecretResolutionError("secret key is required")
            return default
        value = os.getenv(resolved_key, default).strip()
        if required and not value:
            raise SecretResolutionError(f"missing required secret: {resolved_key}")
        return value


@dataclass(slots=True)
class CommandSecretProvider:
    command_template: str
    timeout_sec: float
    env_fallback: bool = True
    _cache: dict[str, str] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def get(self, key: str, *, default: str = "", required: bool = False) -> str:
        resolved_key = key.strip()
        if not resolved_key:
            if required:
                raise SecretResolutionError("secret key is required")
            return default
        with self._lock:
            cached = self._cache.get(resolved_key)
        if cached is not None:
            return cached

        value = self._resolve_via_command(resolved_key)
        if not value and self.env_fallback:
            value = os.getenv(resolved_key, "").strip()
        if not value:
            value = default
        if required and not value:
            raise SecretResolutionError(f"missing required secret: {resolved_key}")
        with self._lock:
            self._cache[resolved_key] = value
        return value

    def _resolve_via_command(self, key: str) -> str:
        command = self.command_template.strip()
        if not command:
            return ""
        argv = [chunk.format(key=key) for chunk in shlex.split(command)]
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()


def build_secret_provider(settings: AppSettings) -> SecretProvider:
    backend = settings.security.secrets.backend.strip().lower()
    if backend in {"", "env"}:
        return EnvSecretProvider()
    if backend == "command":
        return CommandSecretProvider(
            command_template=settings.security.secrets.command_template,
            timeout_sec=settings.security.secrets.command_timeout_sec,
            env_fallback=settings.security.secrets.env_fallback,
        )
    raise SecretResolutionError(f"unsupported secrets backend: {backend}")


def resolve_operational_db_dsn(settings: AppSettings, *, secrets: SecretProvider | None = None) -> str:
    configured = settings.storage.operational_db_dsn.strip()
    if configured:
        return configured
    key = settings.storage.operational_db_dsn_env.strip()
    if not key:
        return ""
    provider = secrets if secrets is not None else build_secret_provider(settings)
    return provider.get(key)
