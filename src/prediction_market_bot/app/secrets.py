from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from prediction_market_bot.app.settings import AppSettings

logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class VaultSecretProvider:
    vault_addr: str
    vault_token: str
    vault_mount: str = "secret"
    vault_path_prefix: str = "prediction-market-bot"
    timeout_sec: float = 5.0
    env_fallback: bool = True
    _cache: dict[str, str] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)
    _env_provider: EnvSecretProvider = field(default_factory=EnvSecretProvider)

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

        value = self._resolve_via_vault(resolved_key)
        if not value and self.env_fallback:
            value = os.getenv(resolved_key, "").strip()
        if not value:
            value = default
        if required and not value:
            raise SecretResolutionError(f"missing required secret: {resolved_key}")
        with self._lock:
            self._cache[resolved_key] = value
        return value

    def _resolve_via_vault(self, key: str) -> str:
        addr = self.vault_addr.rstrip("/")
        url = f"{addr}/v1/{self.vault_mount}/data/{self.vault_path_prefix}/{key}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("X-Vault-Token", self.vault_token)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            data = body.get("data", {}).get("data", {})
            # The value is stored under the "value" key by convention,
            # but fall back to the secret key name itself.
            value = data.get("value", data.get(key, ""))
            if isinstance(value, str):
                return value.strip()
            return str(value).strip() if value else ""
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            logger.warning("Vault lookup failed for key=%s: %s", key, exc)
            return ""
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Vault response parse error for key=%s: %s", key, exc)
            return ""


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
    if backend == "vault":
        vault_addr = os.getenv(
            settings.security.secrets.vault_addr_env, ""
        ).strip()
        vault_token = os.getenv(
            settings.security.secrets.vault_token_env, ""
        ).strip()
        if not vault_addr or not vault_token:
            if settings.security.secrets.env_fallback:
                logger.warning(
                    "Vault credentials missing (%s / %s); falling back to env provider",
                    settings.security.secrets.vault_addr_env,
                    settings.security.secrets.vault_token_env,
                )
                return EnvSecretProvider()
            raise SecretResolutionError(
                "vault backend requires VAULT_ADDR and VAULT_TOKEN environment variables"
            )
        return VaultSecretProvider(
            vault_addr=vault_addr,
            vault_token=vault_token,
            vault_mount=settings.security.secrets.vault_mount,
            vault_path_prefix=settings.security.secrets.vault_path_prefix,
            timeout_sec=settings.security.secrets.vault_timeout_sec,
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
