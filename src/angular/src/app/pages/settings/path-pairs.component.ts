import {Component, OnDestroy, OnInit} from "@angular/core";
import {Subject} from "rxjs/Subject";
import "rxjs/add/operator/takeUntil";
import {Modal} from "ngx-modialog/plugins/bootstrap";

import {
    PathPair,
    PathPairPayload,
    PathPairResult,
    PathPairService
} from "../../services/settings/path-pair.service";
import {NotificationService} from "../../services/utils/notification.service";
import {Notification} from "../../services/utils/notification";
import {Localization} from "../../common/localization";
import {ModalAccessibilityService} from "../../services/utils/modal-accessibility.service";


@Component({
    selector: "app-path-pairs",
    templateUrl: "./path-pairs.component.html",
    styleUrls: ["./path-pairs.component.scss"]
})
export class PathPairsComponent implements OnInit, OnDestroy {
    public pathPairs: PathPair[] = [];
    public isCreating = false;
    public isEditing = false;
    public editingPair: PathPair = null;

    public formName = "";
    public formRemotePath = "";
    public formLocalPath = "";
    public formEnabled = true;
    public formAutoQueue = true;

    private _destroy$ = new Subject<void>();

    constructor(private _pathPairService: PathPairService,
                private _notificationService: NotificationService,
                private _modal: Modal,
                private _modalAccessibility: ModalAccessibilityService) {
    }

    ngOnInit() {
        this._pathPairService.pathPairs.takeUntil(this._destroy$).subscribe({
            next: pathPairs => {
                this.pathPairs = pathPairs;
            }
        });
    }

    ngOnDestroy() {
        this._destroy$.next();
        this._destroy$.complete();
    }

    startCreate() {
        this.isCreating = true;
        this.isEditing = false;
        this.editingPair = null;
        this.clearForm();
    }

    startEdit(pair: PathPair) {
        this.isEditing = true;
        this.isCreating = false;
        this.editingPair = pair;
        this.formName = pair.name;
        this.formRemotePath = pair.remote_path;
        this.formLocalPath = pair.local_path;
        this.formEnabled = pair.enabled;
        this.formAutoQueue = pair.auto_queue;
    }

    cancel() {
        this.isCreating = false;
        this.isEditing = false;
        this.editingPair = null;
        this.clearForm();
    }

    save() {
        if (!this.formRemotePath || !this.formLocalPath) {
            this.showError("Remote path and local path are required");
            return;
        }

        if (this.isCreating) {
            const pair: PathPairPayload = {
                name: this.formName || this.getDefaultName(this.formRemotePath),
                remote_path: this.formRemotePath,
                local_path: this.formLocalPath,
                enabled: this.formEnabled,
                auto_queue: this.formAutoQueue
            };
            this._pathPairService.create(pair).subscribe({
                next: result => {
                    this.showSuccess("Path pair created");
                    this.showWarnings(result.warnings);
                    this.cancel();
                },
                error: error => this.showError("Failed to create: " + error.message)
            });
            return;
        }

        if (this.isEditing && this.editingPair) {
            this._pathPairService.update({
                id: this.editingPair.id,
                name: this.formName || this.getDefaultName(this.formRemotePath),
                remote_path: this.formRemotePath,
                local_path: this.formLocalPath,
                enabled: this.formEnabled,
                auto_queue: this.formAutoQueue
            }).subscribe({
                next: result => {
                    this.showSuccess("Path pair updated");
                    this.showWarnings(result.warnings);
                    this.cancel();
                },
                error: error => this.showError("Failed to update: " + error.message)
            });
        }
    }

    delete(pair: PathPair) {
        const dialogRef = this._modal.confirm()
            .title(Localization.Modal.DELETE_PATH_PAIR_TITLE)
            .okBtn("Delete")
            .okBtnClass("btn btn-danger")
            .cancelBtn("Cancel")
            .cancelBtnClass("btn btn-secondary")
            .isBlocking(false)
            .showClose(false)
            .body(Localization.Modal.DELETE_PATH_PAIR_MESSAGE(pair.name))
            .open();

        this._modalAccessibility.enhance(dialogRef).then(dRef => {
            dRef.result.then(
                () => {
                    this._pathPairService.delete(pair.id).subscribe({
                        next: () => {
                            this.showSuccess("Path pair deleted");
                            if (this.editingPair && this.editingPair.id === pair.id) {
                                this.cancel();
                            }
                        },
                        error: error => this.showError("Failed to delete: " + error.message)
                    });
                },
                () => { return; }
            );
        });
    }

    toggleEnabled(pair: PathPair) {
        this._pathPairService.update({
            id: pair.id,
            name: pair.name,
            remote_path: pair.remote_path,
            local_path: pair.local_path,
            enabled: !pair.enabled,
            auto_queue: pair.auto_queue
        }).subscribe({
            next: result => this.showWarnings(result.warnings),
            error: error => this.showError("Failed to toggle: " + error.message)
        });
    }

    moveUp(index: number) {
        if (index <= 0) {
            return;
        }
        const ids = this.pathPairs.map(pair => pair.id);
        const current = ids[index];
        ids[index] = ids[index - 1];
        ids[index - 1] = current;
        this._pathPairService.reorder(ids).subscribe({
            error: error => this.showError("Failed to reorder: " + error.message)
        });
    }

    moveDown(index: number) {
        if (index >= this.pathPairs.length - 1) {
            return;
        }
        const ids = this.pathPairs.map(pair => pair.id);
        const current = ids[index];
        ids[index] = ids[index + 1];
        ids[index + 1] = current;
        this._pathPairService.reorder(ids).subscribe({
            error: error => this.showError("Failed to reorder: " + error.message)
        });
    }

    private clearForm() {
        this.formName = "";
        this.formRemotePath = "";
        this.formLocalPath = "";
        this.formEnabled = true;
        this.formAutoQueue = true;
    }

    private getDefaultName(remotePath: string): string {
        const parts = remotePath.replace(/\/+$/, "").split("/");
        return parts[parts.length - 1] || "Default";
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

    private showWarnings(warnings: string[]) {
        warnings.forEach(warning => {
            this._notificationService.show(new Notification({
                level: Notification.Level.WARNING,
                text: warning,
                dismissible: true
            }));
        });
    }
}
