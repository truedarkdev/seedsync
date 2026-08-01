import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";
import {TestBed} from "@angular/core/testing";

import {MigrationRecoveryService} from "../../../../services/migration/migration-recovery.service";

describe("MigrationRecoveryService", () => {
    let service: MigrationRecoveryService;
    let http: HttpTestingController;
    const status: any = {
        eligible: true, migration_id: "original-v0.8.6-to-current-v1", backup_id: "original-v0.8.6-to-current-v1-a1",
        source_schema: "original-v0.8.6", target_schema: "seedsync-current-v1", confirmation: "RESTORE"
    };

    beforeEach(() => {
        TestBed.configureTestingModule({imports: [HttpClientTestingModule], providers: [MigrationRecoveryService]});
        service = TestBed.get(MigrationRecoveryService);
        http = TestBed.get(HttpTestingController);
    });
    afterEach(() => http.verify());

    it("loads receipt-bound recovery status", () => {
        let received: any;
        service.loadStatus().subscribe(value => received = value);
        const request = http.expectOne("/server/admin/migration-recovery/v1/status");
        expect(request.request.method).toBe("GET");
        request.flush(status);
        expect(received.backup_id).toBe(status.backup_id);
    });

    it("submits only the confirmation and no-other-instance attestation", () => {
        service.restore(status, status.confirmation).subscribe();
        const request = http.expectOne("/server/admin/migration-recovery/v1/restore");
        expect(request.request.body).toEqual({
            confirmation: status.confirmation,
            other_instances_stopped: true
        });
        request.flush({accepted: true, message: "Restarting"});
    });

    it("accepts only a valid migration checkpoint status when reconnecting", () => {
        let received: any;
        service.checkMigrationCheckpoint().subscribe(value => received = value);
        const request = http.expectOne("/server/migration/v1/status");
        request.flush({
            schema_version: 2, mode: "migration_required", state: "required",
            migration_id: "original-v0.8.6-to-current-v1", source_schema: "original-v0.8.6", target_schema: "current-v1",
            features: [], error: null, retryable: false,
            capabilities: {apply: true, retry: false, continue: false, restore: false},
            normal_startup: {released: false, requires_continue: false},
            backup: {required: true, complete_restore_ready: false, status: "created_before_apply"},
            operation: {status: "idle", message: "Ready"},
            action: {csrf_token: "csrf-proof-0123456789-0123456789", confirmation: "MIGRATE original-v0.8.6-to-current-v1"}, blocker: null
        });
        expect(received.mode).toBe("migration_required");
    });
});
