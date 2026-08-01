export type MigrationState = "required" | "running" | "failed" | "complete";

export interface MigrationFeature {
    key: string;
    title: string;
    summary: string;
}

export interface MigrationStatus {
    schema_version: 2;
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
        apply: boolean;
        retry: boolean;
        continue: boolean;
        restore: false;
    };
    normal_startup: {
        released: boolean;
        requires_continue: boolean;
    };
    backup: {
        required: true;
        complete_restore_ready: boolean;
        status: "created_before_apply" | "ready";
    };
    operation: {
        status: "idle" | "running" | "succeeded" | "failed";
        message: string;
    };
    action: {
        csrf_token: string;
        confirmation: string;
    };
    blocker: string | null;
}
