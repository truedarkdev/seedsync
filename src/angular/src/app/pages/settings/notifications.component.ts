import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import {CommonModule} from "@angular/common";
import {FormsModule} from "@angular/forms";
import {Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";
import {ConfigService} from "../../services/settings/config.service";
import {INotifications} from "../../services/settings/config";
import {NotificationConfigUpdate, NotificationsService} from "../../services/settings/notifications.service";

@Component({
    selector: "app-notifications-settings",
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: "./notifications.component.html",
    styleUrls: ["./notifications.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotificationsComponent implements OnInit, OnDestroy {
    public state: INotifications = null;
    public webhookUrl = "";
    public hmacSecret = "";
    public appriseUrl = "";
    public appriseTag = "";
    public provider: "webhook" | "apprise" = "webhook";
    public enabled = false;
    public allowPrivateNetworks = false;
    public downloadStart = false;
    public downloadComplete = true;
    public extractionComplete = true;
    public deleteComplete = true;
    public clearWebhookUrl = false;
    public clearHmacSecret = false;
    public clearAppriseUrl = false;
    public busy = false;
    public message = "";
    public error = "";
    private _destroy$ = new Subject<void>();

    constructor(private _configService: ConfigService,
                private _notificationsService: NotificationsService,
                private _changeDetector: ChangeDetectorRef) {}

    ngOnInit(): void {
        this._configService.config.pipe(takeUntil(this._destroy$)).subscribe(config => {
            this.state = config ? config.notifications as INotifications : null;
            if (this.state) {
                this.enabled = this.state.enabled;
                this.provider = this.state.provider;
                this.appriseTag = this.state.apprise_tag || "";
                this.allowPrivateNetworks = this.state.allow_private_networks;
                this.downloadStart = this.state.download_start;
                this.downloadComplete = this.state.download_complete;
                this.extractionComplete = this.state.extraction_complete;
                this.deleteComplete = this.state.delete_complete;
            }
            this._changeDetector.markForCheck();
        });
    }

    ngOnDestroy(): void {
        this._destroy$.next();
        this._destroy$.complete();
    }

    save(): void {
        if (!this.state || this.busy) { return; }
        const update: NotificationConfigUpdate = {
            enabled: this.enabled,
            provider: this.provider,
            allow_private_networks: this.allowPrivateNetworks,
            download_start: this.downloadStart,
            download_complete: this.downloadComplete,
            extraction_complete: this.extractionComplete,
            delete_complete: this.deleteComplete,
            apprise_tag: this.appriseTag,
        };
        if (this.webhookUrl.length > 0 || this.clearWebhookUrl) {
            update.webhook_url = this.clearWebhookUrl ? "" : this.webhookUrl;
        }
        if (this.hmacSecret.length > 0 || this.clearHmacSecret) {
            update.hmac_secret = this.clearHmacSecret ? "" : this.hmacSecret;
        }
        if (this.appriseUrl.length > 0 || this.clearAppriseUrl) {
            update.apprise_url = this.clearAppriseUrl ? "" : this.appriseUrl;
        }
        this.run("Notification settings saved.", () => this._notificationsService.update(update), true);
    }

    test(): void {
        if (this.busy) { return; }
        this.run("Test notification delivered.", () => this._notificationsService.test(), false);
    }

    get selectedProviderConfigured(): boolean {
        if (!this.state) { return false; }
        return this.provider === "webhook"
            ? this.state.webhook_url_configured
            : this.state.apprise_url_configured;
    }

    private run(successMessage: string, action: () => any, clearInputs: boolean): void {
        this.busy = true;
        this.message = "";
        this.error = "";
        action().pipe(takeUntil(this._destroy$)).subscribe({
            next: () => {
                this.busy = false;
                this.message = successMessage;
                if (clearInputs) {
                    this.webhookUrl = "";
                    this.hmacSecret = "";
                    this.appriseUrl = "";
                    this.clearWebhookUrl = false;
                    this.clearHmacSecret = false;
                    this.clearAppriseUrl = false;
                }
                this._changeDetector.markForCheck();
            },
            error: () => {
                this.busy = false;
                this.error = "The notification request failed. Check the server log for safe diagnostic details.";
                this._changeDetector.markForCheck();
            },
        });
    }
}
