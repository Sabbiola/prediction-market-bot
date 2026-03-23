from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceOperation(StrEnum):
    BACKFILL = "backfill"
    LIVE_POLLING = "live_polling"
    SEARCH = "search"
    THREAD_CONTEXT_EXPANSION = "thread_context_expansion"


@dataclass(slots=True, frozen=True)
class SourceCapabilities:
    requires_oauth: bool = False
    requires_user_context: bool = False
    supports_backfill: bool = True
    supports_live_polling: bool = True
    supports_search: bool = True
    supports_thread_context_expansion: bool = False

    def supports(self, operation: SourceOperation) -> bool:
        if operation == SourceOperation.BACKFILL:
            return self.supports_backfill
        if operation == SourceOperation.LIVE_POLLING:
            return self.supports_live_polling
        if operation == SourceOperation.SEARCH:
            return self.supports_search
        if operation == SourceOperation.THREAD_CONTEXT_EXPANSION:
            return self.supports_thread_context_expansion
        return False

