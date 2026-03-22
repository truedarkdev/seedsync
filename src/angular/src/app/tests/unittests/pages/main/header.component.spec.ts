import {CommonModule} from "@angular/common";
import {ComponentFixture, TestBed} from "@angular/core/testing";

import {HeaderComponent} from "../../../../pages/main/header.component";
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
        fixture = TestBed.createComponent(HeaderComponent);
    });

    function pushRemoteScanFailureStatus(errorMessage: string) {
        const statusJson: ServerStatusJson = {
            server: {
                up: true,
                error_msg: null
            },
            controller: {
                latest_local_scan_time: null,
                latest_remote_scan_time: "1524743857.3456243",
                latest_remote_scan_failed: true,
                latest_remote_scan_error: errorMessage
            }
        };
        serverStatusService.notifyEvent("status", JSON.stringify(statusJson));
    }

    it("should shorten repetitive remote scan failures while preserving pair attribution", () => {
        pushRemoteScanFailureStatus(
            "Remote scan completed with recoverable errors: " +
            "Failed to scan remote path for pair 'Mom\'s Shows': Connection refused; retrying - ssh: connect to host localhost port 1234: " +
            "Failed to scan remote path for pair 'TV': temporary remote failure"
        );

        fixture.detectChanges();

        const alerts = fixture.nativeElement.querySelectorAll(".alert");
        expect(alerts.length).toBe(1);
        expect(alerts[0].classList.contains("alert-warning")).toBe(true);
        expect(alerts[0].innerHTML).toContain("Lost connection to remote server");
        expect(alerts[0].innerHTML).toContain("Mom's Shows: Connection refused; retrying - ssh: connect to host localhost port 1234:");
        expect(alerts[0].innerHTML).toContain("TV: temporary remote failure");
        expect(alerts[0].innerHTML).not.toContain("Remote scan completed with recoverable errors");
        expect(alerts[0].innerHTML).not.toContain("Failed to scan remote path for pair");
    });
});
