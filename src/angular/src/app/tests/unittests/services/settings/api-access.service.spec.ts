import {TestBed} from "@angular/core/testing";
import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";

import {LoggerService} from "../../../../services/utils/logger.service";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {MockStreamServiceRegistry} from "../../../mocks/mock-stream-service.registry";
import {
    ApiAccessService,
    ApiKeyRecord
} from "../../../../services/settings/api-access.service";


describe("Testing API access service", () => {
    let mockRegistry: MockStreamServiceRegistry;
    let httpMock: HttpTestingController;
    let apiAccessService: ApiAccessService;

    const baseApiKeys: ApiKeyRecord[] = [{
        id: "reader",
        name: "Reader",
        scopes: ["read"],
        created_at: "2026-04-01T00:00:00+00:00",
        updated_at: "2026-04-01T00:00:00+00:00",
        revoked_at: null,
        active: true
    }];

    const revokedApiKeys: ApiKeyRecord[] = [{
        id: "revoked-reader",
        name: "Revoked Reader",
        scopes: ["read"],
        created_at: "2026-04-01T00:00:00+00:00",
        updated_at: "2026-04-01T00:04:00+00:00",
        revoked_at: "2026-04-01T00:04:00+00:00",
        active: false
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

    it("should load api keys on connect", () => {
        let latestKeys: ApiKeyRecord[] = null;

        apiAccessService.apiKeys.subscribe({
            next: keys => latestKeys = keys
        });

        httpMock.expectOne("/server/admin/api-keys/v1").flush({
            keys: baseApiKeys
        });

        expect(latestKeys.length).toBe(1);
        expect(latestKeys[0].name).toBe("Reader");
        httpMock.verify();
    });

    it("should bootstrap the first admin key and refresh api keys", () => {
        let latestKeys: ApiKeyRecord[] = null;

        apiAccessService.apiKeys.subscribe({
            next: keys => latestKeys = keys
        });

        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: []});

        let result = null;
        apiAccessService.bootstrapFirstApiKey("Bootstrap Admin").subscribe({
            next: created => result = created
        });

        const bootstrapRequest = httpMock.expectOne("/server/admin/bootstrap/v1/first-api-key");
        expect(bootstrapRequest.request.method).toBe("POST");
        expect(bootstrapRequest.request.body).toEqual({
            name: "Bootstrap Admin"
        });
        bootstrapRequest.flush({
            key: {
                id: "bootstrap-admin",
                name: "Bootstrap Admin",
                scopes: ["admin"],
                created_at: "2026-04-01T00:15:00+00:00",
                updated_at: "2026-04-01T00:15:00+00:00",
                revoked_at: null,
                active: true
            },
            secret: "bootstrap-secret"
        });

        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        expect(result.key.name).toBe("Bootstrap Admin");
        expect(result.secret).toBe("bootstrap-secret");
        expect(latestKeys.length).toBe(1);
        httpMock.verify();
    });

    it("should create an API key and refresh state", () => {
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

        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        expect(result.key.name).toBe("Writer");
        expect(result.secret).toBe("secret-value");
        httpMock.verify();
    });

    it("should reveal revoked keys only when requested and delete revoked keys", () => {
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        apiAccessService.setIncludeRevokedApiKeys(true);

        const revokedListRequest = httpMock.expectOne(request => {
            return request.url === "/server/admin/api-keys/v1" && request.params.get("include_revoked") === "1";
        });
        revokedListRequest.flush({keys: revokedApiKeys});

        let deleted = false;
        apiAccessService.deleteApiKey("revoked-reader").subscribe({
            next: () => deleted = true
        });

        const deleteRequest = httpMock.expectOne("/server/admin/api-keys/v1/revoked-reader");
        expect(deleteRequest.request.method).toBe("DELETE");
        deleteRequest.flush(null, {status: 204, statusText: "No Content"});

        httpMock.expectOne(request => {
            return request.url === "/server/admin/api-keys/v1" && request.params.get("include_revoked") === "1";
        }).flush({keys: baseApiKeys});

        expect(deleted).toBeTrue();
        httpMock.verify();
    });

    it("should delete an api key without refreshing when asked", () => {
        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});

        let deleted = false;
        apiAccessService.deleteApiKey("revoked-reader", false).subscribe({
            next: () => deleted = true
        });

        const deleteRequest = httpMock.expectOne("/server/admin/api-keys/v1/revoked-reader");
        expect(deleteRequest.request.method).toBe("DELETE");
        deleteRequest.flush(null, {status: 204, statusText: "No Content"});

        expect(deleted).toBeTrue();
        httpMock.expectNone("/server/admin/api-keys/v1");
        httpMock.verify();
    });

    it("should update, rotate, and revoke API keys", () => {
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

        httpMock.expectOne("/server/admin/api-keys/v1").flush({keys: baseApiKeys});
        expect(revoked.active).toBe(false);
        httpMock.verify();
    });
});
