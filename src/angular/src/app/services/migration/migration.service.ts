import {Injectable} from "@angular/core";
import {HttpClient} from "@angular/common/http";
import {Observable} from "rxjs";
import {map} from "rxjs/operators";

import {MigrationFeature, MigrationState, MigrationStatus} from "./migration.model";


const MIGRATION_STATES: MigrationState[] = ["required", "running", "failed", "complete"];

function isObject(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseFeature(value: unknown): MigrationFeature {
    if (!isObject(value) || typeof value.title !== "string" || typeof value.summary !== "string") {
        throw new Error("Malformed migration status response");
    }
    const key = typeof value.key === "string" && value.key.trim().length > 0
        ? value.key
        : value.title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
    return {key, title: value.title, summary: value.summary};
}

export function parseMigrationStatus(value: unknown): MigrationStatus {
    if (!isObject(value) ||
        value.schema_version !== 1 ||
        value.mode !== "migration_required" ||
        typeof value.state !== "string" ||
        !MIGRATION_STATES.includes(value.state as MigrationState) ||
        !(value.migration_id === null || typeof value.migration_id === "string") ||
        !(value.source_schema === null || typeof value.source_schema === "string") ||
        !(value.target_schema === null || typeof value.target_schema === "string") ||
        !Array.isArray(value.features) ||
        !(value.error === null || (isObject(value.error) &&
            typeof value.error.code === "string" && typeof value.error.message === "string")) ||
        typeof value.retryable !== "boolean" ||
        !isObject(value.capabilities) ||
        value.capabilities.apply !== false ||
        value.capabilities.retry !== false ||
        value.capabilities.restore !== false ||
        !isObject(value.backup) ||
        value.backup.required !== true ||
        value.backup.complete_restore_ready !== false ||
        value.backup.status !== "not_ready" ||
        value.blocker !== "complete_backup_restore_not_ready") {
        throw new Error("Malformed migration status response");
    }

    return {
        schema_version: 1,
        mode: "migration_required",
        state: value.state as MigrationState,
        migration_id: value.migration_id as string | null,
        source_schema: value.source_schema as string | null,
        target_schema: value.target_schema as string | null,
        features: value.features.map(parseFeature),
        error: value.error as MigrationStatus["error"],
        retryable: value.retryable,
        capabilities: {apply: false, retry: false, restore: false},
        backup: {required: true, complete_restore_ready: false, status: "not_ready"},
        blocker: "complete_backup_restore_not_ready"
    };
}

@Injectable({providedIn: "root"})
export class MigrationService {
    constructor(private readonly http: HttpClient) {}

    loadStatus(): Observable<MigrationStatus> {
        return this.http.get<unknown>("/server/migration/v1/status").pipe(map(parseMigrationStatus));
    }
}
