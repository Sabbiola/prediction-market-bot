from __future__ import annotations

from dataclasses import dataclass

from .capabilities import SourceCapabilities


@dataclass(slots=True, frozen=True)
class AltDataSourceRegistration:
    source_id: str
    source_class: str
    enabled: bool
    adapter: str
    endpoint_url: str
    credential_env: str
    capabilities: SourceCapabilities

