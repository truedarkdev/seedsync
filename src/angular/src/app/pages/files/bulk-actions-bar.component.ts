import {Component, Input, ChangeDetectionStrategy} from "@angular/core";

import {List} from "immutable";
import {Modal} from "ngx-modialog/plugins/bootstrap";

import {ViewFile} from "../../services/files/view-file";
import {BulkCommandService} from "../../services/server/bulk-command.service";
import {BulkCommandFile} from "../../services/server/bulk-command.service";
import {LoggerService} from "../../services/utils/logger.service";
import {FileSelectionService} from "../../services/files/file-selection.service";
import {Localization} from "../../common/localization";
import {WebReaction} from "../../services/utils/rest.service";
import {ModalAccessibilityService} from "../../services/utils/modal-accessibility.service";

@Component({
    selector: "app-bulk-actions-bar",
    providers: [],
    templateUrl: "./bulk-actions-bar.component.html",
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class BulkActionsBarComponent {
    @Input() selectedFiles: List<ViewFile> = List<ViewFile>([]);

    constructor(private bulkCommandService: BulkCommandService,
                private fileSelectionService: FileSelectionService,
                private logger: LoggerService,
                private modal: Modal,
                private modalAccessibility: ModalAccessibilityService) {}

    public clearSelection() {
        this.fileSelectionService.clear();
    }

    private getSelectedCommandFiles(): BulkCommandFile[] {
        return this.selectedFiles.map(file => ({
            file_id: file.fileId,
            name: file.name
        })).toArray();
    }

    public isQueueable(): boolean {
        return this.selectedFiles.size > 0 && this.selectedFiles.every(file => file.isQueueable);
    }

    public queue() {
        if (!this.isQueueable()) {
            return;
        }
        this.send(this.bulkCommandService.queue(this.getSelectedCommandFiles()));
    }

    public isStoppable(): boolean {
        return this.selectedFiles.size > 0 && this.selectedFiles.every(file => file.isStoppable);
    }

    public stop() {
        if (!this.isStoppable()) {
            return;
        }
        this.send(this.bulkCommandService.stop(this.getSelectedCommandFiles()));
    }

    public isExtractable(): boolean {
        return this.selectedFiles.size > 0
            && this.selectedFiles.every(file => file.isExtractable && file.isArchive);
    }

    public extract() {
        if (!this.isExtractable()) {
            return;
        }
        this.send(this.bulkCommandService.extract(this.getSelectedCommandFiles()));
    }

    public isLocallyDeletable(): boolean {
        return this.selectedFiles.size > 0 && this.selectedFiles.every(file => file.isLocallyDeletable);
    }

    public deleteLocal() {
        const fileNames = this.selectedFiles.map(file => file.name).toArray();
        if (!this.isLocallyDeletable()) {
            return;
        }
        this.showDeleteConfirmation(
            Localization.Modal.DELETE_LOCAL_BULK_TITLE,
            Localization.Modal.DELETE_LOCAL_BULK_MESSAGE(fileNames),
            () => this.send(this.bulkCommandService.deleteLocal(this.getSelectedCommandFiles()))
        );
    }

    public isRemotelyDeletable(): boolean {
        return this.selectedFiles.size > 0 && this.selectedFiles.every(file => file.isRemotelyDeletable);
    }

    public deleteRemote() {
        const fileNames = this.selectedFiles.map(file => file.name).toArray();
        if (!this.isRemotelyDeletable()) {
            return;
        }
        this.showDeleteConfirmation(
            Localization.Modal.DELETE_REMOTE_BULK_TITLE,
            Localization.Modal.DELETE_REMOTE_BULK_MESSAGE(fileNames),
            () => this.send(this.bulkCommandService.deleteRemote(this.getSelectedCommandFiles()))
        );
    }

    private send(request) {
        request.subscribe((reaction: WebReaction) => {
            if (reaction.success) {
                this.logger.info(reaction.data);
                this.fileSelectionService.clear();
            } else {
                this.logger.error(reaction.errorMessage);
            }
        });
    }

    private showDeleteConfirmation(title: string, message: string, callback: () => void) {
        const dialogRef = this.modal.confirm()
            .title(title)
            .okBtn("Delete")
            .okBtnClass("btn btn-danger")
            .cancelBtn("Cancel")
            .cancelBtnClass("btn btn-secondary")
            .isBlocking(false)
            .showClose(false)
            .body(message)
            .open();

        this.modalAccessibility.enhance(dialogRef).then(dRef => {
            dRef.result.then(
                () => { callback(); },
                () => { return; }
            );
        });
    }
}
