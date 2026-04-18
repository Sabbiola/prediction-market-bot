"""Tests for UI auth lockout store injection (REC-06).

Guards against regressing to the original in-process ``_failed_attempts`` dict
that breaks brute-force protection in multi-replica deployments.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prediction_market_bot.ui.auth import InMemoryLockoutStore, UiAuthService


def test_in_memory_lockout_store_initial_state() -> None:
    store = InMemoryLockoutStore()
    assert store.get_count("alice") == 0
    assert store.get_lockout_until("alice") is None


def test_in_memory_lockout_store_increment() -> None:
    store = InMemoryLockoutStore()
    store.increment("alice", None)
    assert store.get_count("alice") == 1
    store.increment("alice", None)
    assert store.get_count("alice") == 2


def test_in_memory_lockout_store_lockout_until_set() -> None:
    store = InMemoryLockoutStore()
    future = datetime.now(UTC) + timedelta(seconds=300)
    store.increment("alice", future)
    assert store.get_lockout_until("alice") == future


def test_in_memory_lockout_store_clear() -> None:
    store = InMemoryLockoutStore()
    store.increment("alice", None)
    store.increment("alice", None)
    store.clear("alice")
    assert store.get_count("alice") == 0
    assert store.get_lockout_until("alice") is None


def test_in_memory_lockout_store_clear_unknown_user_is_noop() -> None:
    store = InMemoryLockoutStore()
    store.clear("nobody")  # must not raise


def test_ui_auth_service_accepts_injected_lockout_store(tmp_path) -> None:
    """A custom lockout store can be injected — future implementations
    (Redis, Postgres) just need to satisfy the protocol."""

    class _CustomStore:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._count: dict[str, int] = {}
            self._lockout: dict[str, datetime | None] = {}

        def get_count(self, username: str) -> int:
            self.calls.append(f"get_count:{username}")
            return self._count.get(username, 0)

        def get_lockout_until(self, username: str) -> datetime | None:
            self.calls.append(f"get_lockout_until:{username}")
            return self._lockout.get(username)

        def increment(self, username: str, lockout_until: datetime | None) -> None:
            self.calls.append(f"increment:{username}")
            self._count[username] = self._count.get(username, 0) + 1
            if lockout_until is not None:
                self._lockout[username] = lockout_until

        def clear(self, username: str) -> None:
            self.calls.append(f"clear:{username}")
            self._count.pop(username, None)
            self._lockout.pop(username, None)

    import json
    from pathlib import Path
    from prediction_market_bot.app.config import load_settings

    # Write minimal test configs
    agents_cfg = tmp_path / "agents.yaml"
    app_cfg = tmp_path / "app.yaml"
    agents_cfg.write_text("agents: {}", encoding="utf-8")
    app_cfg.write_text("", encoding="utf-8")

    settings = load_settings(app_cfg, agents_cfg)
    from prediction_market_bot.app.secrets import EnvSecretProvider
    secrets = EnvSecretProvider()

    custom_store = _CustomStore()
    service = UiAuthService(settings, secrets, lockout_store=custom_store)

    # Verify the injected store is used (not the default in-memory one).
    assert service._lockout is custom_store
