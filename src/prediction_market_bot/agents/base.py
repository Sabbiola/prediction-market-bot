from __future__ import annotations

from abc import ABC


class BaseAgent(ABC):
    """Minimal base contract shared by all runtime agents."""

    name: str = "base-agent"
