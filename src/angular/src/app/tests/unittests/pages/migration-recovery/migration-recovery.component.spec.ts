import {ComponentFixture, TestBed} from "@angular/core/testing";
import {of, throwError} from "rxjs";

import {MigrationRecoveryComponent} from "../../../../pages/migration-recovery/migration-recovery.component";
import {MigrationRecoveryService} from "../../../../services/migration/migration-recovery.service";

describe("MigrationRecoveryComponent", () => {
    let fixture: ComponentFixture<MigrationRecoveryComponent>;
    let service: jasmine.SpyObj<MigrationRecoveryService>;
    const status: any = {
        eligible: true, migration_id: "original-v0.8.6-to-current-v1", backup_id: "original-v0.8.6-to-current-v1-a1",
        source_schema: "original-v0.8.6", target_schema: "seedsync-current-v1", confirmation: "RESTORE original-v0.8.6-to-current-v1"
    };

    beforeEach(() => {
        service = jasmine.createSpyObj<MigrationRecoveryService>("MigrationRecoveryService", [
            "loadStatus", "restore", "checkMigrationCheckpoint"
        ]);
        service.checkMigrationCheckpoint.and.returnValue(throwError(() => new Error("not ready")));
        TestBed.configureTestingModule({
            imports: [MigrationRecoveryComponent],
            providers: [{provide: MigrationRecoveryService, useValue: service}]
        });
    });

    function create(): void {
        fixture = TestBed.createComponent(MigrationRecoveryComponent);
        fixture.detectChanges();
    }

    it("keeps undo gated behind the no-other-instance attestation", () => {
        service.loadStatus.and.returnValue(of(status));
        create();

        const button: HTMLButtonElement = fixture.nativeElement.querySelector(".danger-action");
        const checkbox: HTMLInputElement = fixture.nativeElement.querySelector(".attestation input");
        expect(fixture.nativeElement.textContent).toContain("Undo this upgrade");
        expect(fixture.nativeElement.textContent).toContain(status.backup_id);
        expect(fixture.nativeElement.querySelector("app-sidebar")).toBeNull();
        expect(button.disabled).toBeTrue();

        checkbox.click();
        fixture.componentInstance.confirmationPhrase = status.confirmation;
        fixture.detectChanges();
        expect(button.disabled).toBeFalse();
    });

    it("switches to a reconnecting recovery state only after acceptance", () => {
        service.loadStatus.and.returnValue(of(status));
        service.restore.and.returnValue(of({accepted: true, message: "Restarting"}));
        create();
        fixture.componentInstance.confirmationChecked = true;
        fixture.componentInstance.confirmationPhrase = status.confirmation;
        spyOn<any>(fixture.componentInstance, "navigateToMigrationCheckpoint");
        service.checkMigrationCheckpoint.and.returnValue(of({} as any));
        fixture.componentInstance.requestRestore();
        fixture.detectChanges();

        expect(service.restore).toHaveBeenCalledWith(status, status.confirmation);
        expect((fixture.componentInstance as any).navigateToMigrationCheckpoint).toHaveBeenCalled();
        expect(fixture.nativeElement.textContent).toContain("Restoring your migration backup");
    });

    it("shows an actionable no-mutation status failure", () => {
        service.loadStatus.and.returnValue(throwError(() => new Error("offline")));
        create();

        expect(fixture.nativeElement.textContent).toContain("Recovery status unavailable");
        expect(fixture.nativeElement.textContent).toContain("No configuration was changed");
    });

    it("retries unavailable migration checkpoint connections without leaving recovery", () => {
        jasmine.clock().install();
        try {
            service.loadStatus.and.returnValue(of(status));
            service.restore.and.returnValue(of({accepted: true, message: "Restarting"}));
            create();
            fixture.componentInstance.confirmationChecked = true;
            fixture.componentInstance.confirmationPhrase = status.confirmation;
            fixture.componentInstance.requestRestore();
            expect(service.checkMigrationCheckpoint).toHaveBeenCalledTimes(1);
            jasmine.clock().tick(MigrationRecoveryComponent.RECONNECT_INTERVAL_MS);
            expect(service.checkMigrationCheckpoint).toHaveBeenCalledTimes(2);
            expect(fixture.componentInstance.reconnecting).toBeTrue();
            expect(fixture.componentInstance.reconnectTimedOut).toBeFalse();
        } finally {
            jasmine.clock().uninstall();
        }
    });

    it("shows actionable retry state after bounded reconnect attempts", () => {
        service.loadStatus.and.returnValue(of(status));
        service.restore.and.returnValue(of({accepted: true, message: "Restarting"}));
        create();
        fixture.componentInstance.confirmationChecked = true;
        fixture.componentInstance.confirmationPhrase = status.confirmation;
        fixture.componentInstance.reconnectAttempt = MigrationRecoveryComponent.MAX_RECONNECT_ATTEMPTS - 1;
        fixture.componentInstance.requestRestore();
        fixture.componentInstance.reconnectAttempt = MigrationRecoveryComponent.MAX_RECONNECT_ATTEMPTS - 1;
        (fixture.componentInstance as any).scheduleMigrationCheckpointRetry();
        fixture.detectChanges();

        expect(fixture.componentInstance.reconnectTimedOut).toBeTrue();
        expect(fixture.nativeElement.textContent).toContain("Retry connection check");
    });
});
