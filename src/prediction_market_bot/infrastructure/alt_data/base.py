from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol

from .capabilities import SourceOperation
from .models import AltDataSourceRegistration


class AltDataAdapterError(RuntimeError):
    """Base error for alternative data adapter lifecycle."""


class AltDataCapabilityError(AltDataAdapterError):
    """Raised when a source is used for an unsupported operation."""


class AltDataCredentialError(AltDataAdapterError):
    """Raised when source credentials are missing or invalid for configured capabilities."""


class CredentialResolver(Protocol):
    def __call__(self, env_name: str) -> str:
        ...


@dataclass(slots=True, frozen=True)
class ConfiguredAltDataSourceAdapter:
    registration: AltDataSourceRegistration

    @property
    def source_id(self) -> str:
        return self.registration.source_id

    @property
    def source_class(self) -> str:
        return self.registration.source_class

    @property
    def enabled(self) -> bool:
        return bool(self.registration.enabled)

    def ensure_operation_supported(self, operation: SourceOperation) -> None:
        if self.registration.capabilities.supports(operation):
            return
        raise AltDataCapabilityError(
            f"source={self.source_id} class={self.source_class} does_not_support operation={operation.value}"
        )

    def ensure_credentials(self, credential_resolver: CredentialResolver) -> None:
        capabilities = self.registration.capabilities
        if not capabilities.requires_oauth:
            return
        env_name = self.registration.credential_env.strip()
        if not env_name:
            raise AltDataCredentialError(
                f"source={self.source_id} requires_oauth=true but credential_env is not configured"
            )
        token = credential_resolver(env_name).strip()
        if token:
            return
        raise AltDataCredentialError(
            f"source={self.source_id} requires credential from env={env_name} but value is missing"
        )

    def ensure_user_context(self, user_context_resolver: Callable[[str], bool]) -> None:
        if not self.registration.capabilities.requires_user_context:
            return
        if user_context_resolver(self.source_id):
            return
        raise AltDataCredentialError(
            f"source={self.source_id} requires_user_context=true but user context is unavailable"
        )
