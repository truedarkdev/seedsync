import {ComponentFixture, TestBed, fakeAsync, tick} from "@angular/core/testing";
import {BehaviorSubject, of} from "rxjs";
import {NotificationsComponent} from "../../../../pages/settings/notifications.component";
import {Config} from "../../../../services/settings/config";
import {ConfigService} from "../../../../services/settings/config.service";
import {NotificationsService} from "../../../../services/settings/notifications.service";

class MockConfigService {
    config = new BehaviorSubject(new Config({notifications: {
        enabled: true,
        provider: "webhook",
        webhook_url_configured: true,
        hmac_secret_configured: true,
        apprise_url_configured: true,
        apprise_tag: "seedbox",
        allow_private_networks: false,
        download_start: true,
        download_complete: true,
        extraction_complete: true,
        delete_complete: true,
    }}));
}

class MockNotificationsService {
    update = jasmine.createSpy("update").and.returnValue(of({notifications: {}}));
    test = jasmine.createSpy("test").and.returnValue(of({delivered: true}));
}

describe("Testing notification settings component", () => {
    let fixture: ComponentFixture<NotificationsComponent>;
    let component: NotificationsComponent;
    let service: MockNotificationsService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [NotificationsComponent],
            providers: [
                {provide: ConfigService, useClass: MockConfigService},
                {provide: NotificationsService, useClass: MockNotificationsService},
            ],
        });
        fixture = TestBed.createComponent(NotificationsComponent);
        component = fixture.componentInstance;
        service = TestBed.get(NotificationsService);
        fixture.detectChanges();
    });

    it("shows configured state without hydrating sensitive inputs or sending placeholders", () => {
        const inputs = fixture.nativeElement.querySelectorAll("input[type=url], input[type=password]");
        expect(inputs[0].value).toBe("");
        expect(inputs[1].value).toBe("");

        component.save();

        const update = service.update.calls.mostRecent().args[0];
        expect(update.provider).toBe("webhook");
        expect(update.download_start).toBeTrue();
        expect(update.webhook_url).toBeUndefined();
        expect(update.hmac_secret).toBeUndefined();
        expect(update.apprise_url).toBeUndefined();
        expect(fixture.nativeElement.textContent).toContain("Download started");
    });

    it("sends explicit replacements and invokes the test action", () => {
        component.webhookUrl = "https://hooks.example.test/new";
        component.hmacSecret = "replacement";
        component.save();
        expect(service.update).toHaveBeenCalledWith(jasmine.objectContaining({
            webhook_url: "https://hooks.example.test/new",
            hmac_secret: "replacement",
        }));

        component.test();
        expect(service.test).toHaveBeenCalled();
    });

    it("shows only selected provider fields and sends write-only Apprise input", fakeAsync(() => {
        expect(fixture.nativeElement.querySelector(".webhook-url-input")).not.toBeNull();
        expect(fixture.nativeElement.querySelector(".apprise-url-input")).toBeNull();

        tick();
        const providerSelect = fixture.nativeElement.querySelector("select") as HTMLSelectElement;
        providerSelect.value = "apprise";
        providerSelect.dispatchEvent(new Event("change"));
        fixture.detectChanges();
        tick();

        expect(fixture.nativeElement.querySelector(".webhook-url-input")).toBeNull();
        const appriseInput = fixture.nativeElement.querySelector(".apprise-url-input") as HTMLInputElement;
        expect(appriseInput).not.toBeNull();
        expect(appriseInput.value).toBe("");
        expect(fixture.nativeElement.textContent).toContain("destinations are configured in Apprise");
        expect(fixture.nativeElement.textContent).toContain("authenticated HTTPS");

        component.appriseUrl = "https://apprise.example.test/notify/private-key";
        component.appriseTag = "downloads";
        component.save();

        expect(service.update).toHaveBeenCalledWith(jasmine.objectContaining({
            provider: "apprise",
            apprise_url: "https://apprise.example.test/notify/private-key",
            apprise_tag: "downloads",
        }));
    }));
});
