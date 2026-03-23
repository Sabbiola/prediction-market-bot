from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable

from prediction_market_bot.app.settings import AltDataSourceSettings

from .base import (
    AltDataCapabilityError,
    AltDataCredentialError,
    ConfiguredAltDataSourceAdapter,
    CredentialResolver,
)
from .capabilities import SourceCapabilities, SourceOperation
from .models import AltDataSourceRegistration


def _env_credential_resolver(env_name: str) -> str:
    return os.getenv(env_name, "")


def _default_user_context_resolver(_: str) -> bool:
    return False


@dataclass(slots=True, frozen=True)
class CapabilityValidationIssue:
    source_id: str
    severity: str
    detail: str


class AltDataAdapterRegistry:
    def __init__(
        self,
        registrations: Iterable[AltDataSourceRegistration],
        *,
        credential_resolver: CredentialResolver | None = None,
        user_context_resolver: Callable[[str], bool] | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver or _env_credential_resolver
        self._user_context_resolver = user_context_resolver or _default_user_context_resolver
        self._sources: dict[str, ConfiguredAltDataSourceAdapter] = {}
        for registration in registrations:
            source_id = registration.source_id.strip().lower()
            if not source_id:
                continue
            self._sources[source_id] = ConfiguredAltDataSourceAdapter(registration=registration)

    @classmethod
    def from_source_settings(
        cls,
        source_settings: Iterable[AltDataSourceSettings],
        *,
        credential_resolver: CredentialResolver | None = None,
        user_context_resolver: Callable[[str], bool] | None = None,
    ) -> "AltDataAdapterRegistry":
        registrations: list[AltDataSourceRegistration] = []
        for source in source_settings:
            registrations.append(
                AltDataSourceRegistration(
                    source_id=source.source_id.strip().lower(),
                    source_class=source.source_class.strip().lower(),
                    enabled=bool(source.enabled),
                    adapter=source.adapter.strip().lower(),
                    endpoint_url=source.endpoint_url.strip(),
                    credential_env=source.credential_env.strip(),
                    capabilities=SourceCapabilities(
                        requires_oauth=source.capabilities.requires_oauth,
                        requires_user_context=source.capabilities.requires_user_context,
                        supports_backfill=source.capabilities.supports_backfill,
                        supports_live_polling=source.capabilities.supports_live_polling,
                        supports_search=source.capabilities.supports_search,
                        supports_thread_context_expansion=source.capabilities.supports_thread_context_expansion,
                    ),
                )
            )
        return cls(
            registrations,
            credential_resolver=credential_resolver,
            user_context_resolver=user_context_resolver,
        )

    def list_registered_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources.keys()))

    def list_enabled_ids(self) -> tuple[str, ...]:
        return tuple(sorted(source_id for source_id, source in self._sources.items() if source.enabled))

    def get(self, source_id: str) -> ConfiguredAltDataSourceAdapter:
        normalized = source_id.strip().lower()
        source = self._sources.get(normalized)
        if source is not None:
            return source
        raise KeyError(f"unknown_alt_data_source: {source_id}")

    def require_source_ready(
        self,
        source_id: str,
        *,
        operation: SourceOperation | None = None,
        require_enabled: bool = True,
    ) -> ConfiguredAltDataSourceAdapter:
        source = self.get(source_id)
        if require_enabled and not source.enabled:
            raise AltDataCapabilityError(f"source={source.source_id} is disabled by configuration")
        source.ensure_credentials(self._credential_resolver)
        source.ensure_user_context(self._user_context_resolver)
        if operation is not None:
            source.ensure_operation_supported(operation)
        return source

    def validate_enabled_sources(self, *, operation: SourceOperation | None = None) -> tuple[CapabilityValidationIssue, ...]:
        issues: list[CapabilityValidationIssue] = []
        for source_id in self.list_enabled_ids():
            try:
                self.require_source_ready(source_id, operation=operation)
            except AltDataCredentialError as exc:
                issues.append(
                    CapabilityValidationIssue(
                        source_id=source_id,
                        severity="error",
                        detail=str(exc),
                    )
                )
            except AltDataCapabilityError as exc:
                issues.append(
                    CapabilityValidationIssue(
                        source_id=source_id,
                        severity="error",
                        detail=str(exc),
                    )
                )
        return tuple(issues)


def build_credential_resolver_from_mapping(values: dict[str, str]) -> Callable[[str], str]:
    normalized = {str(key).strip(): str(value).strip() for key, value in values.items()}

    def _resolver(env_name: str) -> str:
        return normalized.get(env_name.strip(), "")

    return _resolver
