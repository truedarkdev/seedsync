import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import {Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";

import {Modal} from "../../services/utils/modal.service";
import {ModalAccessibilityService} from "../../services/utils/modal-accessibility.service";
import {Notification} from "../../services/utils/notification";
import {NotificationService} from "../../services/utils/notification.service";
import {
    ApiAccessMigrationState,
    ApiKeyRecord,
    ApiAccessService,
    LegacyApiTokenState
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
    standalone: false,
    templateUrl: "./api-access.component.html",
    styleUrls: ["./api-access.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class ApiAccessComponent implements OnInit, OnDestroy {
    public readonly scopeOptions: ApiAccessScopeOption[] = [
        {value: "read", label: "Read"},
        {value: "write", label: "Write"},
        {value: "stream", label: "Stream"},
        {value: "admin", label: "Admin"}
    ];

    public migrationState = this._apiAccessService.migrationState;
    public apiKeys = this._apiAccessService.apiKeys;
    public showRevokedKeys = false;

    public createName = "";
    public createScopes: ScopeSelection = this.defaultScopeSelection();

    public editingKeyId: string = null;
    public editingName = "";
    public editingScopes: ScopeSelection = this.defaultScopeSelection();

    public secretReveal: ApiAccessSecretReveal = null;

    private _destroy$ = new Subject<void>();
    private _destroyed = false;

    constructor(private _changeDetector: ChangeDetectorRef,
                private _apiAccessService: ApiAccessService,
                private _notificationService: NotificationService,
                private _modal: Modal,
                private _modalAccessibility: ModalAccessibilityService) {
    }

    ngOnInit() {
        this._apiAccessService.setIncludeRevokedApiKeys(this.showRevokedKeys);
        this._apiAccessService.migrationState.pipe(takeUntil(this._destroy$)).subscribe({
            next: () => this._changeDetector.markForCheck()
        });
        this._apiAccessService.apiKeys.pipe(takeUntil(this._destroy$)).subscribe({
            next: () => this._changeDetector.markForCheck()
        });
    }

    ngOnDestroy() {
        this._destroyed = true;
        this._destroy$.next();
        this._destroy$.complete();
    }

    public startEdit(key: ApiKeyRecord) {
        if (!key.active) {
            return;
        }
        this.editingKeyId = key.id;
        this.editingName = key.name;
        this.editingScopes = this.scopeSelectionFromList(key.scopes);
    }

    public cancelEdit() {
        this.editingKeyId = null;
        this.editingName = "";
        this.editingScopes = this.defaultScopeSelection();
    }

    public createApiKey() {
        const name = this.createName.trim();
        const scopes = this.getSelectedScopes(this.createScopes);
        if (!name) {
            this.showError("API key name cannot be blank");
            return;
        }
        if (scopes.length === 0) {
            this.showError("Choose at least one API key scope");
            return;
        }

        this._apiAccessService.createApiKey(name, scopes).pipe(takeUntil(this._destroy$)).subscribe({
            next: result => {
                this.revealSecret("API key created", `Copy the new secret for ${result.key.name} now.`, result.secret);
                this.createName = "";
                this.createScopes = this.defaultScopeSelection();
                this._changeDetector.markForCheck();
            },
            error: error => this.showError(`Failed to create API key: ${this.describeError(error)}`)
        });
    }

    public saveApiKey(key: ApiKeyRecord) {
        if (!key.active) {
            this.showError("Revoked API keys cannot be edited");
            return;
        }
        const name = this.editingName.trim();
        const scopes = this.getSelectedScopes(this.editingScopes);
        if (!name) {
            this.showError("API key name cannot be blank");
            return;
        }
        if (scopes.length === 0) {
            this.showError("Choose at least one API key scope");
            return;
        }

        this._apiAccessService.updateApiKey(key.id, name, scopes).pipe(takeUntil(this._destroy$)).subscribe({
            next: updated => {
                this.cancelEdit();
                this._changeDetector.markForCheck();
            },
            error: error => this.showError(`Failed to update API key: ${this.describeError(error)}`)
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
        if (!key.active) {
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
                        this.cancelEdit();
                    }
                    this._changeDetector.markForCheck();
                },
                error: error => this.showError(`Failed to revoke API key: ${this.describeError(error)}`)
            })
        );
    }

    public toggleRevokedKeys() {
        this.showRevokedKeys = !this.showRevokedKeys;
        this._apiAccessService.setIncludeRevokedApiKeys(this.showRevokedKeys);
        this._changeDetector.markForCheck();
    }

    public deleteRevokedApiKey(key: ApiKeyRecord) {
        if (key.active) {
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
                        this.cancelEdit();
                    }
                    this._changeDetector.markForCheck();
                },
                error: error => this.showError(`Failed to delete revoked API key: ${this.describeError(error)}`)
            })
        );
    }

    public disableLegacyApiToken() {
        this.confirmAction(
            "Disable Legacy Compatibility",
            "Disable compatibility for general.api_token on external non-admin requests. Existing scoped API keys will continue to work.",
            "Disable",
            "btn btn-warning",
            () => this._apiAccessService.disableLegacyApiToken().pipe(takeUntil(this._destroy$)).subscribe({
                next: state => {
                    this.showMigrationStateMessage(state, "Disabled legacy compatibility");
                    this._changeDetector.markForCheck();
                },
                error: error => this.showError(`Failed to disable legacy compatibility: ${this.describeError(error)}`)
            })
        );
    }

    public clearLegacyApiToken() {
        this.confirmAction(
            "Clear Legacy API Token",
            "Clear general.api_token and disable legacy compatibility. This removes the stored fallback token.",
            "Clear Token",
            "btn btn-danger",
            () => this._apiAccessService.clearLegacyApiToken().pipe(takeUntil(this._destroy$)).subscribe({
                next: state => {
                    this.showMigrationStateMessage(state, "Cleared legacy API token");
                    this._changeDetector.markForCheck();
                },
                error: error => this.showError(`Failed to clear legacy API token: ${this.describeError(error)}`)
            })
        );
    }

    public dismissSecret() {
        this.secretReveal = null;
        this._changeDetector.markForCheck();
    }

    public isEditing(key: ApiKeyRecord): boolean {
        return this.editingKeyId === key.id;
    }

    public getLegacyStateLabel(state: LegacyApiTokenState): string {
        if (!state || !state.configured) {
            return "Cleared";
        }
        if (state.compatibility_enabled) {
            return "Active";
        }
        return "Disabled";
    }

    public getLegacyStateDescription(state: LegacyApiTokenState): string {
        if (!state || !state.configured) {
            return "No legacy token stored";
        }
        if (state.compatibility_enabled) {
            return "general.api_token still works for external non-admin requests";
        }
        return "general.api_token is stored locally but no longer accepted";
    }

    private showMigrationStateMessage(state: ApiAccessMigrationState, successMessage: string) {
        this.showSuccess(successMessage);
        if (state && state.legacy_api_token && state.legacy_api_token.configured && state.legacy_api_token.compatibility_enabled) {
            this.showWarning("Legacy token compatibility is still active");
        }
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

    private showWarning(message: string) {
        this._notificationService.show(new Notification({
            level: Notification.Level.WARNING,
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
