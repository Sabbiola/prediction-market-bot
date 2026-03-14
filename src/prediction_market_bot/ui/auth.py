from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Mapping

from fastapi import HTTPException, Request, status

from prediction_market_bot.app.settings import AppSettings, UiAuthUserSettings


@dataclass(slots=True, frozen=True)
class AuthenticatedUser:
    username: str
    role: str
    session_expires_at: str


class UiAuthService:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.ui_auth.enabled

    def authenticate_credentials(self, *, username: str, password: str) -> AuthenticatedUser | None:
        if not self.enabled:
            return AuthenticatedUser(username="local-admin", role="admin", session_expires_at="")
        user = self._find_user(username)
        if user is None:
            return None
        expected = self._resolve_password(user)
        if not expected:
            return None
        if not compare_digest(password, expected):
            return None
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
        if not username or role not in {"viewer", "operator", "admin"}:
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

    @staticmethod
    def _resolve_password(user: UiAuthUserSettings) -> str:
        if user.password_env:
            return os.getenv(user.password_env, "").strip()
        return user.password.strip()
