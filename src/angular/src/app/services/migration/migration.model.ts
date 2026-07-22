export type MigrationState = "required" | "running" | "failed" | "complete";

export interface MigrationFeature {
    key: string;
    title: string;
    summary: string;
}

export interface MigrationStatus {
    schema_version: 1;
    mode: "migration_required";
    state: MigrationState;
    migration_id: string | null;
    source_schema: string | null;
    target_schema: string | null;
    features: MigrationFeature[];
    error: {
        code: string;
        message: string;
    } | null;
    retryable: boolean;
    capabilities: {
        apply: false;
        retry: false;
        restore: false;
    };
    backup: {
        required: true;
        complete_restore_ready: false;
        status: "not_ready";
    };
    blocker: "complete_backup_restore_not_ready";
}
