import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import {CommonModule} from "@angular/common";
import {FormsModule} from "@angular/forms";
import {from, Subject} from "rxjs";
import {concatMap, finalize, takeUntil} from "rxjs/operators";

import {Modal} from "../../services/utils/modal.service";
import {ModalAccessibilityService} from "../../services/utils/modal-accessibility.service";
import {Notification} from "../../services/utils/notification";
import {NotificationService} from "../../services/utils/notification.service";
import {
    ApiKeyRecord,
    ApiAccessService
} from "../../services/settings/api-access.service";


interface ApiAccessScopeOption {
    value: string;
    label: string;
}

interface ApiAccessSecretReveal {
    title: string;
    message: string;
    secret: string;
}

interface ScopeSelection {
    [scope: string]: boolean;
}

@Component({
    selector: "app-api-access",
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: "./api-access.component.html",
    styleUrls: ["./api-access.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class ApiAccessComponent implements OnInit, OnDestroy {
    private readonly _timestampFormat: Intl.DateTimeFormatOptions = {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit"
    };

    public readonly scopeOptions: ApiAccessScopeOption[] = [
        {value: "read", label: "Read"},
        {value: "write", label: "Write"},
        {value: "stream", label: "Stream"},
        {value: "admin", label: "Admin"}
    ];

    public apiKeys = this._apiAccessService.apiKeys;
    public showRevokedKeys = false;

    public isCreating = false;
    public isEditing = false;
    public isDeletingRevokedApiKeys = false;
    public formName = "";
    public formScopes: ScopeSelection = this.defaultScopeSelection();
    public bootstrapName = "bootstrap-admin";

    public editingKeyId: string = null;

    public secretReveal: ApiAccessSecretReveal = null;

    private _destroy$ = new Subject<void>();
    private _destroyed = false;
    private _expandedKeyIds = new Set<string>();

    constructor(private _changeDetector: ChangeDetectorRef,
                private _apiAccessService: ApiAccessService,
                private _notificationService: NotificationService,
                private _modal: Modal,
                private _modalAccessibility: ModalAccessibilityService) {
    }

    ngOnInit() {
        this._apiAccessService.setIncludeRevokedApiKeys(true);
        this._apiAccessService.apiKeys.pipe(takeUntil(this._destroy$)).subscribe({
            next: () => this._changeDetector.markForCheck()
        });
    }

    ngOnDestroy() {
        this._destroyed = true;
        this._destroy$.next();
        this._destroy$.complete();
    }

    public get isFormOpen(): boolean {
        return this.isCreating || this.isEditing;
    }

    public startCreate() {
        if (this.isFormOpen) {
            return;
        }

        this.isCreating = true;
        this.isEditing = false;
        this.editingKeyId = null;
        this.formName = "";
        this.formScopes = this.defaultScopeSelection();
        this._changeDetector.markForCheck();
    }

    public startEdit(key: ApiKeyRecord) {
        if (this.isFormOpen || !key.active) {
            return;
        }
        this.isCreating = false;
        this.isEditing = true;
        this.editingKeyId = key.id;
        this.formName = key.name;
        this.formScopes = this.scopeSelectionFromList(key.scopes);
        this._changeDetector.markForCheck();
    }

    public cancelForm() {
        this.isCreating = false;
        this.isEditing = false;
        this.editingKeyId = null;
        this.formName = "";
        this.formScopes = this.defaultScopeSelection();
        this._changeDetector.markForCheck();
    }

    public saveForm() {
        const name = this.formName.trim();
        const scopes = this.getSelectedScopes(this.formScopes);

        if (!name) {
            this.showError("API key name cannot be blank");
            return;
        }
        if (scopes.length === 0) {
            this.showError("Choose at least one API key scope");
            return;
        }

        if (this.isCreating) {
            this._apiAccessService.createApiKey(name, scopes).pipe(takeUntil(this._destroy$)).subscribe({
                next: result => {
                    this.revealSecret("API key created", `Copy the new secret for ${result.key.name} now.`, result.secret);
                    this.cancelForm();
                },
                error: error => this.showError(`Failed to create API key: ${this.describeError(error)}`)
            });
            return;
        }

        if (this.isEditing && this.editingKeyId) {
            this._apiAccessService.updateApiKey(this.editingKeyId, name, scopes).pipe(takeUntil(this._destroy$)).subscribe({
                next: () => {
                    this.cancelForm();
                },
                error: error => this.showError(`Failed to update API key: ${this.describeError(error)}`)
            });
        }
    }

    public toggleDetails(key: ApiKeyRecord) {
        if (!key || !key.id) {
            return;
        }

        if (this._expandedKeyIds.has(key.id)) {
            this._expandedKeyIds.delete(key.id);
        } else {
            this._expandedKeyIds.add(key.id);
        }
        this._changeDetector.markForCheck();
    }

    public isDetailsVisible(key: ApiKeyRecord): boolean {
        return !!key && !!key.id && this._expandedKeyIds.has(key.id);
    }

    public formatTimestamp(value: string): string {
        if (!value) {
            return "";
        }

        const parsed = new Date(value);
        if (isNaN(parsed.getTime())) {
            return value;
        }

        return parsed.toLocaleString([], this._timestampFormat);
    }

    public bootstrapFirstApiKey() {
        const name = this.bootstrapName.trim() || "bootstrap-admin";

        this._apiAccessService.bootstrapFirstApiKey(name).pipe(takeUntil(this._destroy$)).subscribe({
            next: result => {
                this.revealSecret("First admin API key created", `Copy the new secret for ${result.key.name} now.`, result.secret);
                this.bootstrapName = "bootstrap-admin";
                this._changeDetector.markForCheck();
            },
            error: error => this.showError(`Failed to bootstrap first admin API key: ${this.describeError(error)}`)
        });
    }

    public rotateApiKey(key: ApiKeyRecord) {
        if (!key.active) {
            return;
        }
        this.confirmAction(
            "Rotate API Key",
            `Rotation reveals a new secret for ${key.name} and immediately retires the old one.`,
            "Rotate",
            "btn btn-warning",
            () => this._apiAccessService.rotateApiKey(key.id).pipe(takeUntil(this._destroy$)).subscribe({
                next: result => {
                    this.revealSecret("API key rotated", `Copy the new secret for ${result.key.name} now.`, result.secret);
                    this._changeDetector.markForCheck();
                },
                error: error => this.showError(`Failed to rotate API key: ${this.describeError(error)}`)
            })
        );
    }

    public revokeApiKey(key: ApiKeyRecord) {
        if (!key.active || this.isFormOpen) {
            return;
        }
        this.confirmAction(
            "Revoke API Key",
            `Are you sure you want to revoke ${key.name}? The secret will stop working immediately.`,
            "Revoke",
            "btn btn-danger",
            () => this._apiAccessService.revokeApiKey(key.id).pipe(takeUntil(this._destroy$)).subscribe({
                next: revoked => {
                    if (this.editingKeyId === revoked.id) {
                        this.cancelForm();
                    }
                    this._changeDetector.markForCheck();
                },
                error: error => this.showError(`Failed to revoke API key: ${this.describeError(error)}`)
            })
        );
    }

    public toggleRevokedKeys() {
        this.showRevokedKeys = !this.showRevokedKeys;
        this._changeDetector.markForCheck();
    }

    public deleteRevokedApiKey(key: ApiKeyRecord) {
        if (key.active || this.isFormOpen) {
            return;
        }
        this.confirmAction(
            "Delete Revoked API Key",
            `Permanently remove revoked key ${key.name}? This cannot be undone.`,
            "Delete",
            "btn btn-danger",
            () => this._apiAccessService.deleteApiKey(key.id).pipe(takeUntil(this._destroy$)).subscribe({
                next: () => {
                    if (this.editingKeyId === key.id) {
                        this.cancelForm();
                    }
                    this._changeDetector.markForCheck();
                },
                error: error => this.showError(`Failed to delete revoked API key: ${this.describeError(error)}`)
            })
        );
    }

    public deleteAllRevokedApiKeys(apiKeys: ApiKeyRecord[]) {
        if (this.isFormOpen || this.isDeletingRevokedApiKeys || !this.showRevokedKeys) {
            return;
        }

        const revokedKeys = this.getRevokedApiKeys(apiKeys);
        if (revokedKeys.length === 0) {
            return;
        }

        this.confirmAction(
            "Delete Revoked API Keys",
            `Permanently remove all ${revokedKeys.length} revoked key${revokedKeys.length === 1 ? "" : "s"}? This cannot be undone. Active keys will stay in place.`,
            "Delete all",
            "btn btn-danger",
            () => {
                this.isDeletingRevokedApiKeys = true;
                this._changeDetector.markForCheck();

                from(revokedKeys).pipe(
                    concatMap(key => this._apiAccessService.deleteApiKey(key.id, false)),
                    takeUntil(this._destroy$),
                    finalize(() => {
                        this.isDeletingRevokedApiKeys = false;
                        if (!this._destroyed) {
                            this._apiAccessService.refresh();
                        }
                        this._changeDetector.markForCheck();
                    })
                ).subscribe({
                    error: error => this.showError(`Failed to delete revoked API keys: ${this.describeError(error)}`)
                });
            }
        );
    }

    public getActiveApiKeyCount(apiKeys: ApiKeyRecord[]): number {
        return (apiKeys || []).filter(key => key.active).length;
    }

    public getRevokedApiKeyCount(apiKeys: ApiKeyRecord[]): number {
        return this.getRevokedApiKeys(apiKeys).length;
    }

    public getVisibleApiKeys(apiKeys: ApiKeyRecord[]): ApiKeyRecord[] {
        return (apiKeys || []).filter(key => key.active || this.showRevokedKeys);
    }

    public getVisibleApiKeyCount(apiKeys: ApiKeyRecord[]): number {
        return this.getVisibleApiKeys(apiKeys).length;
    }

    public getActiveAdminApiKeyCount(apiKeys: ApiKeyRecord[]): number {
        return (apiKeys || []).filter(key => key.active && key.scopes.indexOf("admin") >= 0).length;
    }

    public isBootstrapMode(apiKeys: ApiKeyRecord[]): boolean {
        return this.getActiveAdminApiKeyCount(apiKeys) === 0;
    }

    public dismissSecret() {
        this.secretReveal = null;
        this._changeDetector.markForCheck();
    }

    private revealSecret(title: string, message: string, secret: string) {
        this.secretReveal = {
            title: title,
            message: message,
            secret: secret
        };
    }

    private confirmAction(title: string, body: string, okLabel: string, okClass: string, onConfirm: () => void) {
        const dialogRef = this._modal.confirm()
            .title(title)
            .okBtn(okLabel)
            .okBtnClass(okClass)
            .cancelBtn("Cancel")
            .cancelBtnClass("btn btn-secondary")
            .isBlocking(false)
            .showClose(false)
            .body(body)
            .open();

        this._modalAccessibility.enhance(dialogRef).then(dRef => {
            if (this._destroyed) {
                return;
            }
            dRef.result.then(() => {
                if (!this._destroyed) {
                    onConfirm();
                }
            }, () => { return; });
        });
    }

    private defaultScopeSelection(): ScopeSelection {
        return {
            read: true,
            write: true,
            stream: true,
            admin: false
        };
    }

    private scopeSelectionFromList(scopes: string[]): ScopeSelection {
        const selection = this.defaultScopeSelection();
        Object.keys(selection).forEach(scope => selection[scope] = false);
        (scopes || []).forEach(scope => selection[scope] = true);
        return selection;
    }

    private getSelectedScopes(selection: ScopeSelection): string[] {
        return this.scopeOptions
            .filter(scope => selection[scope.value])
            .map(scope => scope.value);
    }

    private getRevokedApiKeys(apiKeys: ApiKeyRecord[]): ApiKeyRecord[] {
        return (apiKeys || []).filter(key => !key.active);
    }

    private describeError(error: any): string {
        if (error && error.error) {
            if (typeof error.error === "string") {
                return error.error;
            }
            if (error.error.error) {
                return error.error.error;
            }
        }
        if (error && error.message) {
            return error.message;
        }
        return "Unknown error";
    }

    private showSuccess(message: string) {
        this._notificationService.show(new Notification({
            level: Notification.Level.SUCCESS,
            text: message,
            dismissible: true
        }));
    }

    private showError(message: string) {
        this._notificationService.show(new Notification({
            level: Notification.Level.DANGER,
            text: message,
            dismissible: true
        }));
    }
}
