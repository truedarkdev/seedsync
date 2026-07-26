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
        value.schema_version !== 2 ||
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
        typeof value.capabilities.apply !== "boolean" ||
        typeof value.capabilities.retry !== "boolean" ||
        value.capabilities.restore !== false ||
        !isObject(value.backup) ||
        value.backup.required !== true ||
        typeof value.backup.complete_restore_ready !== "boolean" ||
        (value.backup.status !== "created_before_apply" && value.backup.status !== "ready") ||
        !isObject(value.operation) ||
        !["idle", "running", "succeeded", "failed"].includes(value.operation.status as string) ||
        typeof value.operation.message !== "string" ||
        !isObject(value.action) ||
        typeof value.action.csrf_token !== "string" ||
        value.action.csrf_token.length < 20 || value.action.csrf_token.length > 256 ||
        typeof value.action.confirmation !== "string" ||
        value.action.confirmation.length > 256 ||
        (value.capabilities.apply === true && value.state !== "required") ||
        (value.capabilities.retry === true && (value.state !== "failed" || value.retryable !== true)) ||
        ((value.capabilities.apply === true || value.capabilities.retry === true) &&
            value.action.confirmation.length === 0) ||
        !(value.blocker === null || typeof value.blocker === "string")) {
        throw new Error("Malformed migration status response");
    }

    return {
        schema_version: 2,
        mode: "migration_required",
        state: value.state as MigrationState,
        migration_id: value.migration_id as string | null,
        source_schema: value.source_schema as string | null,
        target_schema: value.target_schema as string | null,
        features: value.features.map(parseFeature),
        error: value.error as MigrationStatus["error"],
        retryable: value.retryable,
        capabilities: value.capabilities as MigrationStatus["capabilities"],
        backup: value.backup as MigrationStatus["backup"],
        operation: value.operation as MigrationStatus["operation"],
        action: value.action as MigrationStatus["action"],
        blocker: value.blocker as string | null
    };
}

@Injectable({providedIn: "root"})
export class MigrationService {
    constructor(private readonly http: HttpClient) {}

    loadStatus(): Observable<MigrationStatus> {
        return this.http.get<unknown>("/server/migration/v1/status").pipe(map(parseMigrationStatus));
    }

    apply(status: MigrationStatus): Observable<MigrationStatus> {
        return this.http.post<unknown>(
            "/server/migration/v1/apply",
            {
                confirmation: status.action.confirmation,
                retry: status.capabilities.retry
            },
            {headers: {"X-SeedSync-Migration-CSRF": status.action.csrf_token}}
        ).pipe(map(parseMigrationStatus));
    }
}
