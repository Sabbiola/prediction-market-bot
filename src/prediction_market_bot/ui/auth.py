from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Mapping

from fastapi import HTTPException, Request, status

from prediction_market_bot.app.secrets import SecretProvider
from prediction_market_bot.app.settings import AppSettings, UiAuthUserSettings

_VALID_ROLES = {"viewer", "operator", "admin"}
_PASSWORD_HASH_SCHEME = "pbkdf2_sha256"


@dataclass(slots=True, frozen=True)
class AuthenticatedUser:
    username: str
    role: str
    session_expires_at: str


@dataclass(slots=True)
class _FailedAuthWindow:
    count: int = 0
    lockout_until: datetime | None = None


class UiAuthService:
    def __init__(self, settings: AppSettings, secrets: SecretProvider) -> None:
        self._settings = settings
        self._secrets = secrets
        self._failed_attempts: dict[str, _FailedAuthWindow] = {}

    @property
    def enabled(self) -> bool:
        return self._settings.ui_auth.enabled

    def validate_configuration(self) -> None:
        if not self.enabled:
            return
        users = self._settings.ui_auth.users
        if not users:
            raise ValueError("ui_auth enabled but no users are configured")
        seen: set[str] = set()
        for user in users:
            username = user.username.strip()
            role = user.role.strip().lower()
            if not username:
                raise ValueError("ui_auth users must define non-empty username")
            if role not in _VALID_ROLES:
                raise ValueError(f"ui_auth user '{username}' has unsupported role '{role}'")
            if username in seen:
                raise ValueError(f"ui_auth user '{username}' is duplicated")
            seen.add(username)
            has_plain = bool(self._resolve_password(user))
            has_hash = bool(self._resolve_password_hash(user))
            if self._settings.ui_auth.require_password_hashes:
                if not has_hash:
                    raise ValueError(f"ui_auth user '{username}' must provide a password hash")
                hash_value = self._resolve_password_hash(user)
                if hash_value and not _is_supported_hash(hash_value):
                    raise ValueError(f"ui_auth user '{username}' password hash format is invalid")
                continue
            if not (has_hash or has_plain):
                raise ValueError(f"ui_auth user '{username}' has no credentials configured")
            hash_value = self._resolve_password_hash(user)
            if has_hash and hash_value and not _is_supported_hash(hash_value):
                raise ValueError(f"ui_auth user '{username}' password hash format is invalid")

    def authenticate_credentials(self, *, username: str, password: str) -> AuthenticatedUser | None:
        if not self.enabled:
            return AuthenticatedUser(username="local-admin", role="admin", session_expires_at="")
        user = self._find_user(username)
        if user is None:
            return None
        if self._is_locked_out(user.username):
            return None
        if not self._verify_password(user=user, password=password):
            self._register_failure(user.username)
            return None
        self._clear_failures(user.username)
        expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.ui_auth.session_timeout_sec)
        return AuthenticatedUser(
            username=user.username,
            role=user.role,
            session_expires_at=expires_at.isoformat(),
        )

    def current_user(self, request: Request) -> AuthenticatedUser | None:
        if not self.enabled:
            return AuthenticatedUser(username="local-admin", role="admin", session_expires_at="")
        payload = request.session.get("auth")
        if not isinstance(payload, Mapping):
            return None
        username = self._safe_str(payload.get("username"))
        role = self._safe_str(payload.get("role")).lower()
        expires_at_raw = self._safe_str(payload.get("expires_at"))
        if not username or role not in _VALID_ROLES:
            request.session.clear()
            return None
        expires_at = self._parse_timestamp(expires_at_raw)
        if expires_at is None or datetime.now(UTC) >= expires_at:
            request.session.clear()
            return None

        refreshed_expiry = datetime.now(UTC) + timedelta(seconds=self._settings.ui_auth.session_timeout_sec)
        request.session["auth"] = {
            "username": username,
            "role": role,
            "expires_at": refreshed_expiry.isoformat(),
        }
        return AuthenticatedUser(
            username=username,
            role=role,
            session_expires_at=refreshed_expiry.isoformat(),
        )

    def login(self, request: Request, user: AuthenticatedUser) -> None:
        if not self.enabled:
            return
        request.session["auth"] = {
            "username": user.username,
            "role": user.role,
            "expires_at": user.session_expires_at,
        }

    def logout(self, request: Request) -> None:
        request.session.clear()

    def require_role(self, request: Request, *, allowed_roles: tuple[str, ...]) -> AuthenticatedUser:
        user = self.current_user(request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required")
        if not self.enabled:
            return user
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient_role")
        return user

    def _is_locked_out(self, username: str) -> bool:
        window = self._failed_attempts.get(username.strip())
        if window is None or window.lockout_until is None:
            return False
        if datetime.now(UTC) >= window.lockout_until:
            self._failed_attempts.pop(username.strip(), None)
            return False
        return True

    def _register_failure(self, username: str) -> None:
        key = username.strip()
        window = self._failed_attempts.get(key) or _FailedAuthWindow()
        window.count += 1
        if window.count >= self._settings.ui_auth.max_failed_attempts:
            window.lockout_until = datetime.now(UTC) + timedelta(seconds=self._settings.ui_auth.lockout_seconds)
        self._failed_attempts[key] = window

    def _clear_failures(self, username: str) -> None:
        self._failed_attempts.pop(username.strip(), None)

    def _verify_password(self, *, user: UiAuthUserSettings, password: str) -> bool:
        password_hash = self._resolve_password_hash(user)
        if password_hash:
            return verify_password_hash(password=password, encoded=password_hash)
        if self._settings.ui_auth.require_password_hashes:
            return False
        expected = self._resolve_password(user)
        if not expected:
            return False
        return compare_digest(password, expected)

    def _find_user(self, username: str) -> UiAuthUserSettings | None:
        target = username.strip()
        if not target:
            return None
        for user in self._settings.ui_auth.users:
            if compare_digest(user.username, target):
                return user
        return None

    @staticmethod
    def _safe_str(value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        return ""

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        text = value.strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _resolve_password(self, user: UiAuthUserSettings) -> str:
        if user.password_env:
            return self._secrets.get(user.password_env)
        return user.password.strip()

    def _resolve_password_hash(self, user: UiAuthUserSettings) -> str:
        if user.password_hash_env:
            return self._secrets.get(user.password_hash_env)
        return user.password_hash.strip()


def verify_password_hash(*, password: str, encoded: str) -> bool:
    parsed = _parse_password_hash(encoded)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=len(expected),
    )
    return compare_digest(derived, expected)


def _is_supported_hash(encoded: str) -> bool:
    return _parse_password_hash(encoded) is not None


def _parse_password_hash(encoded: str) -> tuple[int, bytes, bytes] | None:
    parts = encoded.strip().split("$")
    if len(parts) != 4 or parts[0] != _PASSWORD_HASH_SCHEME:
        return None
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        digest = bytes.fromhex(parts[3])
    except (ValueError, TypeError):
        return None
    if iterations < 100_000:
        return None
    if not salt or not digest:
        return None
    return iterations, salt, digest
