import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";
import {TestBed} from "@angular/core/testing";

import {MigrationService} from "../../../../services/migration/migration.service";


describe("MigrationService", () => {
    let service: MigrationService;
    let http: HttpTestingController;

    const payload: any = {
        schema_version: 2,
        mode: "migration_required",
        state: "required",
        migration_id: "original-v0.8.6-to-current-v1",
        source_schema: "original-v0.8.6",
        target_schema: "current-v1",
        features: [{key: "path-pairs", title: "Sync more than one folder", summary: "Preserve transfer roots."}],
        error: null,
        retryable: false,
        capabilities: {apply: true, retry: false, continue: false, restore: false},
        normal_startup: {released: false, requires_continue: false},
        backup: {required: true, complete_restore_ready: false, status: "created_before_apply"},
        operation: {status: "idle", message: "Ready."},
        action: {csrf_token: "csrf-proof-0123456789-0123456789", confirmation: "MIGRATE original-v0.8.6-to-current-v1"},
        blocker: null
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
        expect(received.capabilities).toEqual({apply: true, retry: false, continue: false, restore: false});
    });

    it("rejects a malformed capability", () => {
        let error: any;
        service.loadStatus().subscribe({error: value => error = value});

        const request = http.expectOne("/server/migration/v1/status");
        request.flush({...payload, capabilities: {...payload.capabilities, apply: "yes"}});

        expect(error).toBeDefined();
        expect(error.message).toContain("Malformed migration status response");
    });

    it("sends explicit confirmation and CSRF proof when applying", () => {
        let received: any;
        service.apply(payload).subscribe(status => received = status);

        const request = http.expectOne("/server/migration/v1/apply");
        expect(request.request.method).toBe("POST");
        expect(request.request.headers.get("X-SeedSync-Migration-CSRF")).toBe("csrf-proof-0123456789-0123456789");
        expect(request.request.body).toEqual({
            confirmation: "MIGRATE original-v0.8.6-to-current-v1",
            retry: false
        });
        request.flush({...payload, state: "running", capabilities: {apply: false, retry: false, continue: false, restore: false},
            operation: {status: "running", message: "Running."}, blocker: "migration_running"});
        expect(received.operation.status).toBe("running");
    });

    it("derives a stable feature key when a schema-v2 feature omits its key", () => {
        let received: any;
        service.loadStatus().subscribe(status => received = status);

        const request = http.expectOne("/server/migration/v1/status");
        request.flush({...payload, features: [{title: "Secure access", summary: "Legacy payload."}]});

        expect(received.features[0].key).toBe("secure-access");
    });

    it("posts the durable continue action with CSRF proof", () => {
        let received: any;
        service.continue({...payload, state: "complete", capabilities: {apply: false, retry: false, continue: true, restore: false},
            normal_startup: {released: false, requires_continue: true}})
            .subscribe(status => received = status);

        const request = http.expectOne("/server/migration/v1/continue");
        expect(request.request.method).toBe("POST");
        expect(request.request.body).toEqual({});
        expect(request.request.headers.get("X-SeedSync-Migration-CSRF")).toBe("csrf-proof-0123456789-0123456789");
        request.flush({...payload, state: "complete", capabilities: {apply: false, retry: false, continue: false, restore: false},
            normal_startup: {released: true, requires_continue: false}, blocker: "normal_startup_pending"});

        expect(received.normal_startup.released).toBeTrue();
    });

    it("probes normal bootstrap as text while the runtime restarts", () => {
        let completed = false;
        service.probeNormalStartup().subscribe(() => completed = true);

        const request = http.expectOne("/bootstrap");
        expect(request.request.method).toBe("GET");
        expect(request.request.responseType).toBe("text");
        request.flush("<html>bootstrap</html>");

        expect(completed).toBeTrue();
    });
});
