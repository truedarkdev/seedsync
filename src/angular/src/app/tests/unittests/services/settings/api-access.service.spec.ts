import {TestBed} from "@angular/core/testing";
import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";

import {LoggerService} from "../../../../services/utils/logger.service";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {MockStreamServiceRegistry} from "../../../mocks/mock-stream-service.registry";
import {
    ApiAccessService,
    ApiAccessMigrationState,
    ApiKeyRecord
} from "../../../../services/settings/api-access.service";


describe("Testing API access service", () => {
    let mockRegistry: MockStreamServiceRegistry;
    let httpMock: HttpTestingController;
    let apiAccessService: ApiAccessService;

    const baseMigrationState: ApiAccessMigrationState = {
        legacy_api_token: {
            configured: true,
            compatibility_enabled: true,
            state: "enabled",
            accepted_for_external_non_admin: true
        },
        api_keys: {
            total: 1,
            active: 1,
            revoked: 0
        }
    };

    const baseApiKeys: ApiKeyRecord[] = [{
        id: "reader",
        name: "Reader",
        scopes: ["read"],
        created_at: "2026-04-01T00:00:00+00:00",
        updated_at: "2026-04-01T00:00:00+00:00",
        revoked_at: null,
        active: true
    }];

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [
                HttpClientTestingModule
            ],
            providers: [
                ApiAccessService,
                LoggerService,
                ConnectedService,
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry}
            ]
        });

        mockRegistry = TestBed.get(StreamServiceRegistry);
        httpMock = TestBed.get(HttpTestingController);
        apiAccessService = TestBed.get(ApiAccessService);

        mockRegistry.connect();
        apiAccessService.onInit();
    });

    it("should create an instance", () => {
        expect(apiAccessService).toBeDefined();
    });

    it("should load migration state and API keys on connect", () => {
        let latestMigrationState: ApiAccessMigrationState = null;
        let latestKeys: ApiKeyRecord[] = null;

        apiAccessService.migrationState.subscribe({
            next: state => latestMigrationState = state
        });
        apiAccessService.apiKeys.subscribe({
            next: keys => latestKeys = keys
        });

        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({
            keys: baseApiKeys
        });

        expect(latestMigrationState.legacy_api_token.state).toBe("enabled");
        expect(latestKeys.length).toBe(1);
        expect(latestKeys[0].name).toBe("Reader");
        httpMock.verify();
    });

    it("should create an API key and refresh state", () => {
        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        let result = null;
        apiAccessService.createApiKey("Writer", ["read", "write"]).subscribe({
            next: created => result = created
        });

        const createRequest = httpMock.expectOne("/server/admin/api-keys/v1");
        expect(createRequest.request.method).toBe("POST");
        expect(createRequest.request.body).toEqual({
            name: "Writer",
            scopes: ["read", "write"]
        });
        createRequest.flush({
            key: {
                id: "writer",
                name: "Writer",
                scopes: ["read", "write"],
                created_at: "2026-04-01T00:01:00+00:00",
                updated_at: "2026-04-01T00:01:00+00:00",
                revoked_at: null,
                active: true
            },
            secret: "secret-value"
        });

        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        expect(result.key.name).toBe("Writer");
        expect(result.secret).toBe("secret-value");
        httpMock.verify();
    });

    it("should update, rotate, and revoke API keys", () => {
        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        let updated = null;
        apiAccessService.updateApiKey("reader", "Reader v2", ["read", "stream"]).subscribe({
            next: key => updated = key
        });

        const updateRequest = httpMock.expectOne("/server/admin/api-keys/v1/reader");
        expect(updateRequest.request.method).toBe("PUT");
        updateRequest.flush({
            key: {
                id: "reader",
                name: "Reader v2",
                scopes: ["read", "stream"],
                created_at: "2026-04-01T00:00:00+00:00",
                updated_at: "2026-04-01T00:02:00+00:00",
                revoked_at: null,
                active: true
            }
        });

        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});
        expect(updated.name).toBe("Reader v2");

        let rotated = null;
        apiAccessService.rotateApiKey("reader").subscribe({
            next: result => rotated = result
        });

        const rotateRequest = httpMock.expectOne("/server/admin/api-keys/v1/reader/rotate");
        expect(rotateRequest.request.method).toBe("POST");
        rotateRequest.flush({
            key: {
                id: "reader",
                name: "Reader v2",
                scopes: ["read", "stream"],
                created_at: "2026-04-01T00:00:00+00:00",
                updated_at: "2026-04-01T00:03:00+00:00",
                revoked_at: null,
                active: true
            },
            secret: "new-secret-value"
        });

        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});
        expect(rotated.secret).toBe("new-secret-value");

        let revoked = null;
        apiAccessService.revokeApiKey("reader").subscribe({
            next: key => revoked = key
        });

        const revokeRequest = httpMock.expectOne("/server/admin/api-keys/v1/reader/revoke");
        expect(revokeRequest.request.method).toBe("POST");
        revokeRequest.flush({
            key: {
                id: "reader",
                name: "Reader v2",
                scopes: ["read", "stream"],
                created_at: "2026-04-01T00:00:00+00:00",
                updated_at: "2026-04-01T00:04:00+00:00",
                revoked_at: "2026-04-01T00:04:00+00:00",
                active: false
            }
        });

        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});
        expect(revoked.active).toBe(false);
        httpMock.verify();
    });

    it("should disable and clear legacy token compatibility", () => {
        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        let disabledState = null;
        apiAccessService.disableLegacyApiToken().subscribe({
            next: state => disabledState = state
        });

        const disableRequest = httpMock.expectOne("/server/admin/migration/v1/legacy-api-token/disable");
        expect(disableRequest.request.method).toBe("POST");
        disableRequest.flush({
            legacy_api_token: {
                configured: true,
                compatibility_enabled: false,
                state: "disabled",
                accepted_for_external_non_admin: false
            },
            api_keys: {
                total: 1,
                active: 1,
                revoked: 0
            }
        });

        httpMock.expectOne("/server/admin/migration/v1").flush({
            legacy_api_token: {
                configured: true,
                compatibility_enabled: false,
                state: "disabled",
                accepted_for_external_non_admin: false
            },
            api_keys: {
                total: 1,
                active: 1,
                revoked: 0
            }
        });

        expect(disabledState.legacy_api_token.compatibility_enabled).toBe(false);

        let clearedState = null;
        apiAccessService.clearLegacyApiToken().subscribe({
            next: state => clearedState = state
        });

        const clearRequest = httpMock.expectOne("/server/admin/migration/v1/legacy-api-token/clear");
        expect(clearRequest.request.method).toBe("POST");
        clearRequest.flush({
            legacy_api_token: {
                configured: false,
                compatibility_enabled: false,
                state: "cleared",
                accepted_for_external_non_admin: false
            },
            api_keys: {
                total: 1,
                active: 1,
                revoked: 0
            }
        });

        httpMock.expectOne("/server/admin/migration/v1").flush({
            legacy_api_token: {
                configured: false,
                compatibility_enabled: false,
                state: "cleared",
                accepted_for_external_non_admin: false
            },
            api_keys: {
                total: 1,
                active: 1,
                revoked: 0
            }
        });

        expect(clearedState.legacy_api_token.configured).toBe(false);
        httpMock.verify();
    });

    it("should preserve successful migration state if the follow-up refresh fails", () => {
        let latestMigrationState: ApiAccessMigrationState = null;

        apiAccessService.migrationState.subscribe({
            next: state => latestMigrationState = state
        });

        httpMock.expectOne("/server/admin/migration/v1").flush(baseMigrationState);
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        apiAccessService.disableLegacyApiToken().subscribe();

        const disableRequest = httpMock.expectOne("/server/admin/migration/v1/legacy-api-token/disable");
        disableRequest.flush({
            legacy_api_token: {
                configured: true,
                compatibility_enabled: false,
                state: "disabled",
                accepted_for_external_non_admin: false
            },
            api_keys: {
                total: 1,
                active: 1,
                revoked: 0
            }
        });

        httpMock.expectOne("/server/admin/migration/v1").flush("refresh failed", {
            status: 500,
            statusText: "Server Error"
        });

        expect(latestMigrationState).not.toBeNull();
        expect(latestMigrationState.legacy_api_token.state).toBe("disabled");
        expect(latestMigrationState.legacy_api_token.compatibility_enabled).toBe(false);
        httpMock.verify();
    });
});
