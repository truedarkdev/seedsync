import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";
import {TestBed} from "@angular/core/testing";

import {MigrationService} from "../../../../services/migration/migration.service";


describe("MigrationService", () => {
    let service: MigrationService;
    let http: HttpTestingController;

    const payload: any = {
        schema_version: 1,
        mode: "migration_required",
        state: "required",
        migration_id: "original-v0.8.6-to-current-v1",
        source_schema: "original-v0.8.6",
        target_schema: "current-v1",
        features: [{key: "path-pairs", title: "Sync more than one folder", summary: "Preserve transfer roots."}],
        error: null,
        retryable: false,
        capabilities: {apply: false, retry: false, restore: false},
        backup: {required: true, complete_restore_ready: false, status: "not_ready"},
        blocker: "complete_backup_restore_not_ready"
    };

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [HttpClientTestingModule],
            providers: [MigrationService]
        });
        service = TestBed.get(MigrationService);
        http = TestBed.get(HttpTestingController);
    });

    afterEach(() => http.verify());

    it("requests and validates the migration-only status contract", () => {
        let received: any;
        service.loadStatus().subscribe(status => received = status);

        const request = http.expectOne("/server/migration/v1/status");
        expect(request.request.method).toBe("GET");
        request.flush(payload);

        expect(received.state).toBe("required");
        expect(received.features[0].key).toBe("path-pairs");
        expect(received.capabilities).toEqual({apply: false, retry: false, restore: false});
    });

    it("rejects a response that claims migration capability", () => {
        let error: any;
        service.loadStatus().subscribe({error: value => error = value});

        const request = http.expectOne("/server/migration/v1/status");
        request.flush({...payload, capabilities: {...payload.capabilities, apply: true}});

        expect(error).toBeDefined();
        expect(error.message).toContain("Malformed migration status response");
    });

    it("derives a stable feature key for an older schema-v1 response", () => {
        let received: any;
        service.loadStatus().subscribe(status => received = status);

        const request = http.expectOne("/server/migration/v1/status");
        request.flush({...payload, features: [{title: "Secure access", summary: "Legacy payload."}]});

        expect(received.features[0].key).toBe("secure-access");
    });
});
