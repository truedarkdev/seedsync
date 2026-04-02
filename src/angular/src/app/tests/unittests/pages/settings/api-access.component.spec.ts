import {CommonModule} from "@angular/common";
import {ComponentFixture, TestBed, fakeAsync, flushMicrotasks} from "@angular/core/testing";
import {FormsModule} from "@angular/forms";
import {BehaviorSubject, of} from "rxjs";
import {Modal} from "../../../../services/utils/modal.service";

import {ApiAccessComponent} from "../../../../pages/settings/api-access.component";
import {
    ApiAccessMigrationState,
    ApiAccessService,
    ApiKeyRecord
} from "../../../../services/settings/api-access.service";
import {NotificationService} from "../../../../services/utils/notification.service";
import {ModalAccessibilityService} from "../../../../services/utils/modal-accessibility.service";


const activeApiKeys: ApiKeyRecord[] = [{
    id: "reader",
    name: "Reader",
    scopes: ["read"],
    created_at: "2026-04-01T00:00:00+00:00",
    updated_at: "2026-04-01T00:00:00+00:00",
    revoked_at: null,
    active: true
}];

const revokedApiKey: ApiKeyRecord = {
    id: "revoked-reader",
    name: "Revoked Reader",
    scopes: ["read"],
    created_at: "2026-04-01T00:05:00+00:00",
    updated_at: "2026-04-01T00:06:00+00:00",
    revoked_at: "2026-04-01T00:06:00+00:00",
    active: false
};

const bootstrapMigrationState: ApiAccessMigrationState = {
    legacy_api_token: {
        configured: false,
        compatibility_enabled: false,
        state: "cleared",
        accepted_for_external_non_admin: false
    },
    api_keys: {
        total: 0,
        active: 0,
        active_admin: 0,
        revoked: 0
    }
};

const normalMigrationState: ApiAccessMigrationState = {
    legacy_api_token: {
        configured: true,
        compatibility_enabled: true,
        state: "enabled",
        accepted_for_external_non_admin: true
    },
    api_keys: {
        total: 1,
        active: 1,
        active_admin: 1,
        revoked: 0
    }
};

const nonAdminOnlyMigrationState: ApiAccessMigrationState = {
    legacy_api_token: {
        configured: false,
        compatibility_enabled: false,
        state: "cleared",
        accepted_for_external_non_admin: false
    },
    api_keys: {
        total: 1,
        active: 1,
        active_admin: 0,
        revoked: 0
    }
};


class MockApiAccessService {
    migrationState = new BehaviorSubject<ApiAccessMigrationState>(null);
    apiKeys = new BehaviorSubject<ApiKeyRecord[]>(null);
    refresh = jasmine.createSpy("refresh").and.callFake(() => {
        this.migrationState.next(normalMigrationState);
        this.apiKeys.next(activeApiKeys);
    });
    setIncludeRevokedApiKeys = jasmine.createSpy("setIncludeRevokedApiKeys").and.callFake((includeRevoked: boolean) => {
        if (includeRevoked) {
            this.apiKeys.next([activeApiKeys[0], revokedApiKey]);
        } else {
            this.apiKeys.next(activeApiKeys);
        }
    });

    bootstrapFirstApiKey = jasmine.createSpy("bootstrapFirstApiKey").and.callFake((name: string) => {
        this.refresh();
        return of({
            key: {
                id: "bootstrap-admin",
                name: name,
                scopes: ["admin"],
                created_at: "2026-04-01T00:20:00+00:00",
                updated_at: "2026-04-01T00:20:00+00:00",
                revoked_at: null,
                active: true
            },
            secret: "bootstrap-secret"
        });
    });

    createApiKey = jasmine.createSpy("createApiKey").and.returnValue(of({
        key: {
            id: "writer",
            name: "Writer",
            scopes: ["read", "write", "stream"],
            created_at: "2026-04-01T00:10:00+00:00",
            updated_at: "2026-04-01T00:10:00+00:00",
            revoked_at: null,
            active: true
        },
        secret: "writer-secret"
    }));
    updateApiKey = jasmine.createSpy("updateApiKey").and.returnValue(of({
        id: "reader",
        name: "Reader updated",
        scopes: ["read", "stream"],
        created_at: "2026-04-01T00:00:00+00:00",
        updated_at: "2026-04-01T00:11:00+00:00",
        revoked_at: null,
        active: true
    }));
    rotateApiKey = jasmine.createSpy("rotateApiKey").and.returnValue(of({
        key: {
            id: "reader",
            name: "Reader",
            scopes: ["read"],
            created_at: "2026-04-01T00:00:00+00:00",
            updated_at: "2026-04-01T00:12:00+00:00",
            revoked_at: null,
            active: true
        },
        secret: "rotated-secret"
    }));
    revokeApiKey = jasmine.createSpy("revokeApiKey").and.returnValue(of({
        id: "reader",
        name: "Reader",
        scopes: ["read"],
        created_at: "2026-04-01T00:00:00+00:00",
        updated_at: "2026-04-01T00:13:00+00:00",
        revoked_at: "2026-04-01T00:13:00+00:00",
        active: false
    }));
    deleteApiKey = jasmine.createSpy("deleteApiKey").and.callFake((keyId: string) => {
        if (keyId === revokedApiKey.id) {
            this.apiKeys.next(activeApiKeys);
        }
        return of(void 0);
    });
    disableLegacyApiToken = jasmine.createSpy("disableLegacyApiToken").and.returnValue(of({
        legacy_api_token: {
            configured: true,
            compatibility_enabled: false,
            state: "disabled",
            accepted_for_external_non_admin: false
        },
        api_keys: {
            total: 1,
            active: 1,
            active_admin: 1,
            revoked: 0
        }
    }));
    clearLegacyApiToken = jasmine.createSpy("clearLegacyApiToken").and.returnValue(of({
        legacy_api_token: {
            configured: false,
            compatibility_enabled: false,
            state: "cleared",
            accepted_for_external_non_admin: false
        },
        api_keys: {
            total: 1,
            active: 1,
            active_admin: 1,
            revoked: 0
        }
    }));
}

class MockNotificationService {
    show = jasmine.createSpy("show");
}

class MockDialogResult {
    then(resolve: Function, reject: Function) {
        resolve();
        return this;
    }
}

class MockDialogBuilder {
    title() { return this; }
    okBtn() { return this; }
    okBtnClass() { return this; }
    cancelBtn() { return this; }
    cancelBtnClass() { return this; }
    isBlocking() { return this; }
    showClose() { return this; }
    body() { return this; }
    open() {
        return Promise.resolve({
            result: new MockDialogResult()
        });
    }
}

class MockModal {
    confirm() {
        return new MockDialogBuilder();
    }
}

class MockModalAccessibilityService {
    enhance = jasmine.createSpy("enhance").and.callFake((dialogRefPromise: Promise<any>) => dialogRefPromise);
}

describe("Testing API access component", () => {
    let component: ApiAccessComponent;
    let fixture: ComponentFixture<ApiAccessComponent>;
    let apiAccessService: MockApiAccessService;
    let notificationService: MockNotificationService;

    const migrationState: ApiAccessMigrationState = {
        legacy_api_token: {
            configured: true,
            compatibility_enabled: true,
            state: "enabled",
            accepted_for_external_non_admin: true
        },
        api_keys: {
            total: 2,
            active: 1,
            active_admin: 1,
            revoked: 1
        }
    };

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [
                CommonModule,
                FormsModule
            ],
            declarations: [ApiAccessComponent],
            providers: [
                {provide: ApiAccessService, useClass: MockApiAccessService},
                {provide: NotificationService, useClass: MockNotificationService},
                {provide: Modal, useClass: MockModal},
                {provide: ModalAccessibilityService, useClass: MockModalAccessibilityService}
            ]
        });

        fixture = TestBed.createComponent(ApiAccessComponent);
        component = fixture.componentInstance;
        apiAccessService = TestBed.get(ApiAccessService);
        notificationService = TestBed.get(NotificationService);
        apiAccessService.migrationState.next(migrationState);
        apiAccessService.apiKeys.next(activeApiKeys);
        fixture.detectChanges();
    });

    it("should render the migration banner and the API key list", () => {
        const host: HTMLElement = fixture.nativeElement;

        expect(host.textContent).toContain("API Access");
        expect(host.textContent).toContain("Legacy token compatibility is active.");
        expect(host.textContent).toContain("Reader");
        expect(host.querySelector(".key-item:not(.revoked) .delete-key-btn")).toBeNull();
        expect(host.textContent).toContain("1 revoked key hidden");
        expect(host.querySelector(".toggle-revoked-keys-btn")).not.toBeNull();
        expect(host.querySelector(".disable-legacy-btn")).not.toBeNull();
        expect(host.querySelector(".clear-legacy-btn")).not.toBeNull();
    });

    it("should render the first-admin bootstrap card when no admin exists", () => {
        apiAccessService.migrationState.next(bootstrapMigrationState);
        apiAccessService.apiKeys.next([]);
        fixture.detectChanges();

        const host: HTMLElement = fixture.nativeElement;

        expect(host.textContent).toContain("Bootstrap First Admin");
        expect(host.textContent).toContain("Create the first admin key");
        expect(host.querySelector(".bootstrap-first-admin-btn")).not.toBeNull();
        expect(host.querySelector(".create-key-btn")).toBeNull();
        expect(host.querySelector(".key-list")).toBeNull();
    });

    it("should stay in bootstrap mode when only non-admin api keys exist", () => {
        apiAccessService.migrationState.next(nonAdminOnlyMigrationState);
        apiAccessService.apiKeys.next([]);
        fixture.detectChanges();

        const host: HTMLElement = fixture.nativeElement;

        expect(host.textContent).toContain("Bootstrap First Admin");
        expect(host.querySelector(".bootstrap-first-admin-btn")).not.toBeNull();
        expect(host.querySelector(".create-key-btn")).toBeNull();
    });

    it("should reveal revoked keys on demand and allow permanent deletion", fakeAsync(() => {
        const host: HTMLElement = fixture.nativeElement;

        expect(host.textContent).not.toContain("Revoked Reader");

        const revealButton = host.querySelector(".toggle-revoked-keys-btn") as HTMLButtonElement;
        revealButton.click();
        fixture.detectChanges();

        expect(apiAccessService.setIncludeRevokedApiKeys).toHaveBeenCalledWith(true);
        expect(host.textContent).toContain("Revoked Reader");
        expect(host.querySelector(".key-item.revoked .edit-key-btn")).toBeNull();
        expect(host.querySelector(".key-item.revoked .rotate-key-btn")).toBeNull();
        expect(host.querySelector(".key-item.revoked .revoke-key-btn")).toBeNull();

        const deleteButton = host.querySelector(".key-item.revoked .delete-key-btn") as HTMLButtonElement;
        deleteButton.click();
        flushMicrotasks();
        fixture.detectChanges();

        expect(apiAccessService.deleteApiKey).toHaveBeenCalledWith("revoked-reader");
        expect(notificationService.show).not.toHaveBeenCalled();
        expect(host.textContent).not.toContain("Revoked Reader");
    }));

    it("should create a key and reveal the returned secret", () => {
        apiAccessService.createApiKey.and.returnValue(of({
            key: {
                id: "writer",
                name: "<img src=x onerror=alert(1)>",
                scopes: ["read", "write", "stream"],
                created_at: "2026-04-01T00:10:00+00:00",
                updated_at: "2026-04-01T00:10:00+00:00",
                revoked_at: null,
                active: true
            },
            secret: "writer-secret"
        }));
        component.createName = "Writer";
        component.createScopes = {
            read: true,
            write: true,
            stream: true,
            admin: false
        };
        fixture.detectChanges();

        component.createApiKey();
        fixture.detectChanges();

        expect(apiAccessService.createApiKey).toHaveBeenCalledWith(
            "Writer",
            ["read", "write", "stream"]
        );
        expect(notificationService.show).not.toHaveBeenCalled();
        expect(component.secretReveal.secret).toBe("writer-secret");
        expect(fixture.nativeElement.textContent).toContain("API key created");
        expect(component.createName).toBe("");
    });

    it("should update an API key from the inline edit form", () => {
        apiAccessService.updateApiKey.and.returnValue(of({
            id: "reader",
            name: "<svg onload=alert(1)>",
            scopes: ["read", "stream"],
            created_at: "2026-04-01T00:00:00+00:00",
            updated_at: "2026-04-01T00:11:00+00:00",
            revoked_at: null,
            active: true
        }));
        component.startEdit(activeApiKeys[0]);
        fixture.detectChanges();

        component.editingName = "Reader updated";
        component.editingScopes = {
            read: true,
            write: false,
            stream: true,
            admin: false
        };
        fixture.detectChanges();

        component.saveApiKey(activeApiKeys[0]);
        fixture.detectChanges();

        expect(apiAccessService.updateApiKey).toHaveBeenCalledWith(
            "reader",
            "Reader updated",
            ["read", "stream"]
        );
        expect(notificationService.show).not.toHaveBeenCalled();
        expect(component.editingKeyId).toBe(null);
    });

    it("should rotate and revoke keys from the list actions", fakeAsync(() => {
        apiAccessService.rotateApiKey.and.returnValue(of({
            key: {
                id: "reader",
                name: "<marquee>bad</marquee>",
                scopes: ["read"],
                created_at: "2026-04-01T00:00:00+00:00",
                updated_at: "2026-04-01T00:12:00+00:00",
                revoked_at: null,
                active: true
            },
            secret: "rotated-secret"
        }));
        apiAccessService.revokeApiKey.and.returnValue(of({
            id: "reader",
            name: "<script>alert(1)</script>",
            scopes: ["read"],
            created_at: "2026-04-01T00:00:00+00:00",
            updated_at: "2026-04-01T00:13:00+00:00",
            revoked_at: "2026-04-01T00:13:00+00:00",
            active: false
        }));
        const host: HTMLElement = fixture.nativeElement;

        const rotateButton = host.querySelector(".rotate-key-btn") as HTMLButtonElement;
        rotateButton.click();
        flushMicrotasks();
        fixture.detectChanges();

        expect(apiAccessService.rotateApiKey).toHaveBeenCalledWith("reader");
        expect(notificationService.show).not.toHaveBeenCalled();
        expect(component.secretReveal.secret).toBe("rotated-secret");

        const revokeButton = host.querySelector(".revoke-key-btn") as HTMLButtonElement;
        revokeButton.click();
        flushMicrotasks();
        fixture.detectChanges();

        expect(apiAccessService.revokeApiKey).toHaveBeenCalledWith("reader");
        expect(notificationService.show).not.toHaveBeenCalled();
    }));

    it("should trigger the legacy token controls", fakeAsync(() => {
        const host: HTMLElement = fixture.nativeElement;

        const disableButton = host.querySelector(".disable-legacy-btn") as HTMLButtonElement;
        disableButton.click();
        flushMicrotasks();
        fixture.detectChanges();

        expect(apiAccessService.disableLegacyApiToken).toHaveBeenCalled();

        const clearButton = host.querySelector(".clear-legacy-btn") as HTMLButtonElement;
        clearButton.click();
        flushMicrotasks();
        fixture.detectChanges();

        expect(apiAccessService.clearLegacyApiToken).toHaveBeenCalled();
    }));

    it("should bootstrap the first admin key and transition to the normal API access view", fakeAsync(() => {
        apiAccessService.migrationState.next(bootstrapMigrationState);
        apiAccessService.apiKeys.next([]);
        fixture.detectChanges();

        component.bootstrapName = "Bootstrap Admin";
        fixture.detectChanges();

        const host: HTMLElement = fixture.nativeElement;
        const bootstrapButton = host.querySelector(".bootstrap-first-admin-btn") as HTMLButtonElement;
        bootstrapButton.click();
        flushMicrotasks();
        fixture.detectChanges();

        expect(apiAccessService.bootstrapFirstApiKey).toHaveBeenCalledWith("Bootstrap Admin");
        expect(apiAccessService.refresh).toHaveBeenCalled();
        expect(component.secretReveal.secret).toBe("bootstrap-secret");
        expect(host.textContent).toContain("Create API Key");
        expect(host.querySelector(".bootstrap-first-admin-btn")).toBeNull();
        expect(host.querySelector(".create-key-btn")).not.toBeNull();
    }));
});
