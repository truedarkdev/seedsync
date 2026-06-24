import {CommonModule} from "@angular/common";
import {ComponentFixture, TestBed} from "@angular/core/testing";

import {HeaderComponent} from "../../../../pages/main/header.component";
import {Localization} from "../../../../common/localization";
import {NotificationService} from "../../../../services/utils/notification.service";
import {ServerStatusJson} from "../../../../services/server/server-status";
import {ServerStatusService} from "../../../../services/server/server-status.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {LoggerService} from "../../../../services/utils/logger.service";


class MockStreamServiceRegistry {
    serverStatusService = TestBed.get(ServerStatusService);
}


describe("Testing header component", () => {
    let fixture: ComponentFixture<HeaderComponent>;
    let serverStatusService: ServerStatusService;
    let notificationService: NotificationService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [HeaderComponent],
            imports: [CommonModule],
            providers: [
                LoggerService,
                NotificationService,
                ServerStatusService,
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry}
            ]
        });

        serverStatusService = TestBed.get(ServerStatusService);
        notificationService = TestBed.get(NotificationService);
        spyOn(notificationService, "show").and.callThrough();
        spyOn(notificationService, "hide").and.callThrough();
        fixture = TestBed.createComponent(HeaderComponent);
    });

    function pushStatus(statusJson: ServerStatusJson) {
        serverStatusService.notifyEvent("status", JSON.stringify(statusJson));
        fixture.detectChanges();
    }

    function getAlerts(): HTMLElement[] {
        return Array.from(fixture.nativeElement.querySelectorAll(".alert")) as HTMLElement[];
    }

    function createStatus(
        serverUp: boolean,
        errorMessage: string,
        latestRemoteScanTime: string = null,
        latestRemoteScanFailed: boolean = false,
        latestRemoteScanError: string = null
    ): ServerStatusJson {
        return {
            server: {
                up: serverUp,
                error_msg: errorMessage
            },
            controller: {
                latest_local_scan_time: null,
                latest_remote_scan_time: latestRemoteScanTime,
                latest_remote_scan_failed: latestRemoteScanFailed,
                latest_remote_scan_error: latestRemoteScanError
            }
        };
    }

    it("should switch between waiting and server-down notifications as status changes", () => {
        pushStatus(createStatus(true, null));

        expect(getAlerts().length).toBe(1);
        expect(getAlerts()[0].classList.contains("alert-info")).toBe(true);
        expect(getAlerts()[0].textContent).toContain(Localization.Notification.STATUS_REMOTE_SCAN_WAITING);
        expect(notificationService.show).toHaveBeenCalledTimes(1);
        expect(notificationService.hide).not.toHaveBeenCalled();

        pushStatus(createStatus(false, "SeedSync service unavailable"));

        expect(getAlerts().length).toBe(1);
        expect(getAlerts()[0].classList.contains("alert-danger")).toBe(true);
        expect(getAlerts()[0].textContent).toContain("SeedSync service unavailable");
        expect(notificationService.show).toHaveBeenCalledTimes(2);
        expect(notificationService.hide).toHaveBeenCalledTimes(1);

        pushStatus(createStatus(true, null, "1524743857.3456243"));

        expect(getAlerts().length).toBe(0);
        expect(notificationService.show).toHaveBeenCalledTimes(2);
        expect(notificationService.hide).toHaveBeenCalledTimes(2);
    });

    it("should keep waiting and remote scan error notifications coexisting without duplicates", () => {
        const initialError = "Remote scan completed with recoverable errors: " +
            "Failed to scan remote path for pair 'Mom\'s Shows': Connection refused; retrying - ssh: connect to host localhost port 1234: " +
            "Failed to scan remote path for pair 'TV': temporary remote failure";
        const updatedError = "Remote scan completed with recoverable errors: " +
            "Failed to scan remote path for pair 'TV': retry failed";

        pushStatus(createStatus(true, null, null, true, initialError));

        expect(getAlerts().length).toBe(2);
        expect(getAlerts()[0].classList.contains("alert-warning")).toBe(true);
        expect(getAlerts()[0].innerHTML).toContain("Mom's Shows: Connection refused; retrying - ssh: connect to host localhost port 1234:");
        expect(getAlerts()[0].innerHTML).toContain("TV: temporary remote failure");
        expect(getAlerts()[0].innerHTML).not.toContain("Remote scan completed with recoverable errors");
        expect(getAlerts()[0].innerHTML).not.toContain("Failed to scan remote path for pair");
        expect(getAlerts()[1].classList.contains("alert-info")).toBe(true);
        expect(getAlerts()[1].textContent).toContain(Localization.Notification.STATUS_REMOTE_SCAN_WAITING);
        expect(notificationService.show).toHaveBeenCalledTimes(2);
        expect(notificationService.hide).not.toHaveBeenCalled();

        pushStatus(createStatus(true, null, null, true, initialError));

        expect(getAlerts().length).toBe(2);
        expect(notificationService.show).toHaveBeenCalledTimes(2);
        expect(notificationService.hide).not.toHaveBeenCalled();

        pushStatus(createStatus(true, null, null, true, updatedError));

        expect(getAlerts().length).toBe(2);
        expect(getAlerts()[0].classList.contains("alert-warning")).toBe(true);
        expect(getAlerts()[0].innerHTML).toContain("TV: retry failed");
        expect(getAlerts()[0].innerHTML).not.toContain("temporary remote failure");
        expect(getAlerts()[1].classList.contains("alert-info")).toBe(true);
        expect(getAlerts()[1].textContent).toContain(Localization.Notification.STATUS_REMOTE_SCAN_WAITING);
        expect(notificationService.show).toHaveBeenCalledTimes(3);
        expect(notificationService.hide).toHaveBeenCalledTimes(1);
    });
});
