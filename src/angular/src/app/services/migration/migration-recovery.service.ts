import {Injectable} from "@angular/core";
import {HttpClient} from "@angular/common/http";
import {Observable} from "rxjs";
import {map} from "rxjs/operators";

import {MigrationRecoveryAccepted, MigrationRecoveryStatus} from "./migration-recovery.model";
import {MigrationStatus} from "./migration.model";
import {parseMigrationStatus} from "./migration.service";

@Injectable({providedIn: "root"})
export class MigrationRecoveryService {
    constructor(private readonly http: HttpClient) {}

    loadStatus(): Observable<MigrationRecoveryStatus> {
        return this.http.get<MigrationRecoveryStatus>("/server/admin/migration-recovery/v1/status");
    }

    restore(status: MigrationRecoveryStatus, confirmation: string): Observable<MigrationRecoveryAccepted> {
        return this.http.post<MigrationRecoveryAccepted>(
            "/server/admin/migration-recovery/v1/restore",
            {
                confirmation,
                other_instances_stopped: true
            }
        );
    }

    checkMigrationCheckpoint(): Observable<MigrationStatus> {
        return this.http.get<unknown>("/server/migration/v1/status").pipe(map(parseMigrationStatus));
    }
}
