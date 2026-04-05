"""operational_migrations.py — backward-compatible re-exports.

All symbols that were previously defined here are re-exported from the two
focused submodules:

  - migrations_registry  : data types, SQL definitions, migration list
  - migrations_runner    : init / upgrade / status runner functions
"""
from __future__ import annotations

from .migrations_registry import (
    BASELINE_0001,
    MIGRATION_REGISTRY,
    MigrationDialect,
    OperationalMigration,
    OperationalMigrationError,
    OperationalSchemaStatus,
    latest_operational_schema_version,
    operational_migrations,
)
from .migrations_runner import (
    current_operational_schema_version,
    get_operational_schema_status,
    init_operational_schema,
    upgrade_operational_schema,
)

__all__ = [
    "BASELINE_0001",
    "MIGRATION_REGISTRY",
    "MigrationDialect",
    "OperationalMigration",
    "OperationalMigrationError",
    "OperationalSchemaStatus",
    "current_operational_schema_version",
    "get_operational_schema_status",
    "init_operational_schema",
    "latest_operational_schema_version",
    "operational_migrations",
    "upgrade_operational_schema",
]
