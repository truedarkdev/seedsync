import {TestBed} from "@angular/core/testing";
import {HttpClientTestingModule, HttpTestingController} from "@angular/common/http/testing";
import {ConfigService} from "../../../../services/settings/config.service";
import {NotificationsService} from "../../../../services/settings/notifications.service";

describe("Testing notifications service", () => {
    let service: NotificationsService;
    let http: HttpTestingController;
    const configService = {refresh: jasmine.createSpy("refresh")};

    beforeEach(() => {
        configService.refresh.calls.reset();
        TestBed.configureTestingModule({
            imports: [HttpClientTestingModule],
            providers: [NotificationsService, {provide: ConfigService, useValue: configService}],
        });
        service = TestBed.get(NotificationsService);
        http = TestBed.get(HttpTestingController);
    });

    afterEach(() => http.verify());

    it("posts atomic admin config and refreshes redacted state", () => {
        const update = {
            enabled: true, provider: "webhook" as const,
            webhook_url: "https://hooks.example.test/seed", hmac_secret: "new-secret",
            apprise_tag: "",
            allow_private_networks: false, download_complete: true,
            extraction_complete: true, delete_complete: true,
        };
        service.update(update).subscribe();
        const request = http.expectOne("/server/admin/notifications/v1/config");
        expect(request.request.method).toBe("POST");
        expect(request.request.body).toEqual(update);
        request.flush({notifications: {enabled: true}});
        expect(configService.refresh).toHaveBeenCalled();
    });

    it("posts the dedicated admin test action", () => {
        let delivered = false;
        service.test().subscribe(response => delivered = response.delivered);
        const request = http.expectOne("/server/admin/notifications/v1/test");
        expect(request.request.method).toBe("POST");
        expect(request.request.body).toEqual({});
        request.flush({delivered: true});
        expect(delivered).toBeTrue();
    });
});
