export interface MigrationRecoveryStatus {
    eligible: boolean;
    reason?: string;
    migration_id?: string;
    source_schema?: string;
    target_schema?: string;
    backup_id?: string;
    confirmation?: string;
}

export interface MigrationRecoveryAccepted {
    accepted: true;
    message: string;
}
