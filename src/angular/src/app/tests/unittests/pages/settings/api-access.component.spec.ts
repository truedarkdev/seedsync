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
import {Notification} from "../../../../services/utils/notification";


class MockApiAccessService {
    migrationState = new BehaviorSubject<ApiAccessMigrationState>(null);
    apiKeys = new BehaviorSubject<ApiKeyRecord[]>(null);

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
            total: 1,
            active: 1,
            revoked: 0
        }
    };

    const apiKeys: ApiKeyRecord[] = [{
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
        apiAccessService.apiKeys.next(apiKeys);
        fixture.detectChanges();
    });

    it("should render the migration banner and the API key list", () => {
        const host: HTMLElement = fixture.nativeElement;

        expect(host.textContent).toContain("API Access");
        expect(host.textContent).toContain("Legacy token compatibility is active.");
        expect(host.textContent).toContain("Reader");
        expect(host.querySelector(".disable-legacy-btn")).not.toBeNull();
        expect(host.querySelector(".clear-legacy-btn")).not.toBeNull();
    });

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
        expect(notificationService.show).toHaveBeenCalled();
        expect((notificationService.show.calls.mostRecent().args[0] as Notification).text).toBe("Created API key");
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
        component.startEdit(apiKeys[0]);
        fixture.detectChanges();

        component.editingName = "Reader updated";
        component.editingScopes = {
            read: true,
            write: false,
            stream: true,
            admin: false
        };
        fixture.detectChanges();

        component.saveApiKey(apiKeys[0]);
        fixture.detectChanges();

        expect(apiAccessService.updateApiKey).toHaveBeenCalledWith(
            "reader",
            "Reader updated",
            ["read", "stream"]
        );
        expect((notificationService.show.calls.mostRecent().args[0] as Notification).text).toBe("Updated API key");
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
        expect((notificationService.show.calls.mostRecent().args[0] as Notification).text).toBe("Rotated API key");
        expect(component.secretReveal.secret).toBe("rotated-secret");

        const revokeButton = host.querySelector(".revoke-key-btn") as HTMLButtonElement;
        revokeButton.click();
        flushMicrotasks();
        fixture.detectChanges();

        expect(apiAccessService.revokeApiKey).toHaveBeenCalledWith("reader");
        expect((notificationService.show.calls.mostRecent().args[0] as Notification).text).toBe("Revoked API key");
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
});
