"""Selected-major configuration migrations."""

from .coordinator import (
    MigrationBlockedError,
    MigrationCoordinator,
    MigrationDecision,
    MigrationFeature,
    MigrationSpec,
    MigrationState,
    ValidatedBackupReader,
    default_migration_registry,
)
from .backup_restore import BackupRestoreError

__all__ = [
    "MigrationBlockedError",
    "MigrationCoordinator",
    "MigrationDecision",
    "MigrationFeature",
    "MigrationSpec",
    "MigrationState",
    "ValidatedBackupReader",
    "default_migration_registry",
    "BackupRestoreError",
]
