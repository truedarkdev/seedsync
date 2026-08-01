import {CommonModule} from "@angular/common";
import {ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";

import {MigrationRecoveryStatus} from "../../services/migration/migration-recovery.model";
import {MigrationRecoveryService} from "../../services/migration/migration-recovery.service";

@Component({
    selector: "app-root",
    standalone: true,
    imports: [CommonModule],
    templateUrl: "./migration-recovery.component.html",
    styleUrls: ["./migration-recovery.component.scss"]
})
export class MigrationRecoveryComponent implements OnInit, OnDestroy {
    static readonly RECONNECT_INTERVAL_MS = 750;
    static readonly MAX_RECONNECT_ATTEMPTS = 40;
    status: MigrationRecoveryStatus | null = null;
    loading = true;
    loadError = false;
    confirmationChecked = false;
    confirmationPhrase = "";
    actionBusy = false;
    actionError = "";
    reconnecting = false;
    reconnectTimedOut = false;
    reconnectAttempt = 0;
    private reconnectTimer: number | null = null;

    constructor(
        private readonly recoveryService: MigrationRecoveryService,
        private readonly changeDetector: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.refresh();
    }

    ngOnDestroy(): void {
        if (this.reconnectTimer !== null) {
            window.clearTimeout(this.reconnectTimer);
        }
    }

    refresh(): void {
        this.loading = true;
        this.loadError = false;
        this.actionError = "";
        this.recoveryService.loadStatus().subscribe({
            next: status => {
                this.status = status;
                this.loading = false;
                this.changeDetector.markForCheck();
            },
            error: () => {
                this.status = null;
                this.loading = false;
                this.loadError = true;
                this.changeDetector.markForCheck();
            }
        });
    }

    get canRestore(): boolean {
        return !!this.status?.eligible && this.confirmationChecked &&
            this.confirmationPhrase === this.status.confirmation && !this.actionBusy;
    }

    requestRestore(): void {
        if (!this.status || !this.canRestore) {
            return;
        }
        this.actionBusy = true;
        this.actionError = "";
        this.recoveryService.restore(this.status, this.confirmationPhrase).subscribe({
            next: accepted => {
                this.actionBusy = false;
                this.reconnecting = true;
                this.reconnectTimedOut = false;
                this.reconnectAttempt = 0;
                this.changeDetector.markForCheck();
                this.checkMigrationCheckpoint();
            },
            error: error => {
                this.actionBusy = false;
                this.actionError = error?.error?.error || "SeedSync did not accept the recovery request. Recheck the recovery status before trying again.";
                this.changeDetector.markForCheck();
            }
        });
    }

    retryReconnect(): void {
        if (!this.reconnecting) {
            return;
        }
        this.reconnectTimedOut = false;
        this.reconnectAttempt = 0;
        this.checkMigrationCheckpoint();
    }

    private checkMigrationCheckpoint(): void {
        if (!this.reconnecting || this.reconnectTimedOut) {
            return;
        }
        this.recoveryService.checkMigrationCheckpoint().subscribe({
            next: () => this.navigateToMigrationCheckpoint(),
            error: () => this.scheduleMigrationCheckpointRetry()
        });
    }

    private scheduleMigrationCheckpointRetry(): void {
        this.reconnectAttempt += 1;
        if (this.reconnectAttempt >= MigrationRecoveryComponent.MAX_RECONNECT_ATTEMPTS) {
            this.reconnectTimedOut = true;
            this.changeDetector.markForCheck();
            return;
        }
        this.reconnectTimer = window.setTimeout(() => {
            this.reconnectTimer = null;
            this.checkMigrationCheckpoint();
        }, MigrationRecoveryComponent.RECONNECT_INTERVAL_MS);
        this.changeDetector.markForCheck();
    }

    private navigateToMigrationCheckpoint(): void {
        window.location.replace("/migration");
    }
}
