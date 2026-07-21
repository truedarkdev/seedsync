"""Selected-major configuration migrations."""

from .coordinator import (
    MigrationBlockedError,
    MigrationCoordinator,
    MigrationDecision,
    MigrationFeature,
    MigrationSpec,
    MigrationState,
    default_migration_registry,
)

__all__ = [
    "MigrationBlockedError",
    "MigrationCoordinator",
    "MigrationDecision",
    "MigrationFeature",
    "MigrationSpec",
    "MigrationState",
    "default_migration_registry",
]
