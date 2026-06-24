import {
    Component, Input, Output, ChangeDetectionStrategy,
    EventEmitter, OnChanges, SimpleChanges, ViewChild, ChangeDetectorRef
} from "@angular/core";

import {Modal} from "../../services/utils/modal.service";

import {ViewFile} from "../../services/files/view-file";
import {Localization} from "../../common/localization";
import {ViewFileOptions} from "../../services/files/view-file-options";
import {ModalAccessibilityService} from "../../services/utils/modal-accessibility.service";

@Component({
    selector: "app-file",
    standalone: false,
    providers: [],
    templateUrl: "./file.component.html",
    styleUrls: ["./file.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class FileComponent implements OnChanges {
    // Make ViewFile optionType accessible from template
    ViewFile = ViewFile;

    // Make FileAction accessible from template
    FileAction = FileAction;

    // Expose min function for template
    min = Math.min;

    // Entire div element
    @ViewChild("fileElement") fileElement: any;

    @Input() file: ViewFile;
    @Input() options: ViewFileOptions;
    @Input() isBulkSelected = false;
    @Input() showActions = true;

    @Output() queueEvent = new EventEmitter<ViewFile>();
    @Output() stopEvent = new EventEmitter<ViewFile>();
    @Output() extractEvent = new EventEmitter<ViewFile>();
    @Output() deleteLocalEvent = new EventEmitter<ViewFile>();
    @Output() deleteRemoteEvent = new EventEmitter<ViewFile>();
    @Output() validateEvent = new EventEmitter<ViewFile>();
    @Output() toggleSelectionEvent = new EventEmitter<ViewFile>();

    // Indicates an active action on-going
    activeAction: FileAction = null;

    constructor(private modal: Modal,
                private modalAccessibility: ModalAccessibilityService,
                private _changeDetector: ChangeDetectorRef) {}

    ngOnChanges(changes: SimpleChanges): void {
        if (changes.file == null) {
            return;
        }

        const oldFile: ViewFile = changes.file.previousValue;
        const newFile: ViewFile = changes.file.currentValue;
        if (oldFile != null && newFile != null) {
            const oldFileKey = oldFile.fileId || oldFile.name;
            const newFileKey = newFile.fileId || newFile.name;

            if (oldFileKey !== newFileKey) {
                this.resetActiveAction();
            } else if (oldFile.status !== newFile.status) {
                this.resetActiveAction();
            } else if (this.activeAction === FileAction.DELETE_REMOTE &&
                       oldFile.isRemotelyDeletable && !newFile.isRemotelyDeletable) {
                this.resetActiveAction();
            } else if (this.activeAction === FileAction.DELETE_LOCAL &&
                       oldFile.isLocallyDeletable && !newFile.isLocallyDeletable) {
                this.resetActiveAction();
            }

            // Scroll into view if this file is selected and not already in viewport
            const fileElement = this.fileElement && this.fileElement.nativeElement;
            if (newFile.isSelected && fileElement != null && !FileComponent.isElementInViewport(fileElement)) {
                fileElement.scrollIntoView();
            }
        }
    }

    showDeleteConfirmation(title: string, message: string, callback: () => void) {
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

        this.modalAccessibility.enhance(dialogRef).then( dRef => {
           dRef.result.then(
               () => { callback(); },
               () => { return; }
           );
        });
    }

    isQueueable() {
        return this.activeAction == null && this.file != null && this.file.isQueueable;
    }

    isStoppable() {
        return this.activeAction == null && this.file != null && this.file.isStoppable;
    }

    isExtractable() {
        return this.activeAction == null &&
            this.file != null &&
            this.file.isExtractable &&
            this.file.isArchive;
    }

    isLocallyDeletable() {
        return this.activeAction == null && this.file != null && this.file.isLocallyDeletable;
    }

    isRemotelyDeletable() {
        return this.activeAction == null && this.file != null && this.file.isRemotelyDeletable;
    }

    isValidatable() {
        return this.activeAction == null && this.file != null && this.file.isValidatable;
    }

    onQueue(file: ViewFile) {
        if (!this.isQueueable() || file == null) {
            return;
        }

        this.activeAction = FileAction.QUEUE;
        // Pass to parent component
        this.queueEvent.emit(file);
    }

    onStop(file: ViewFile) {
        if (!this.isStoppable() || file == null) {
            return;
        }

        this.activeAction = FileAction.STOP;
        // Pass to parent component
        this.stopEvent.emit(file);
    }

    onExtract(file: ViewFile) {
        if (!this.isExtractable() || file == null) {
            return;
        }

        this.activeAction = FileAction.EXTRACT;
        // Pass to parent component
        this.extractEvent.emit(file);
    }

    onDeleteLocal(file: ViewFile) {
        if (!this.isLocallyDeletable() || file == null) {
            return;
        }

        this.showDeleteConfirmation(
            Localization.Modal.DELETE_LOCAL_TITLE,
            Localization.Modal.DELETE_LOCAL_MESSAGE(file.name),
            () => {
                this.activeAction = FileAction.DELETE_LOCAL;
                // Pass to parent component
                this.deleteLocalEvent.emit(file);
            }
        );
    }

    onDeleteRemote(file: ViewFile) {
        if (!this.isRemotelyDeletable() || file == null) {
            return;
        }

        this.showDeleteConfirmation(
            Localization.Modal.DELETE_REMOTE_TITLE,
            Localization.Modal.DELETE_REMOTE_MESSAGE(file.name),
            () => {
                this.activeAction = FileAction.DELETE_REMOTE;
                // Pass to parent component
                this.deleteRemoteEvent.emit(file);
            }
        );
    }

    onValidate(file: ViewFile) {
        if (!this.isValidatable() || file == null) {
            return;
        }

        this.activeAction = FileAction.VALIDATE;
        this.validateEvent.emit(file);
    }

    // Late async callbacks can arrive after this row has been rebound by the
    // virtual scroll, so only clear when the row still represents the same
    // file and the same in-flight action.
    resetActiveAction(forFile?: ViewFile, forAction?: FileAction): void {
        if (forFile != null) {
            const currentFileKey = this.file != null ? (this.file.fileId || this.file.name) : null;
            const targetFileKey = forFile.fileId || forFile.name;

            if (currentFileKey !== targetFileKey || this.activeAction !== forAction) {
                return;
            }
        }

        this.activeAction = null;
        this._changeDetector.markForCheck();
    }

    onToggleSelection(file: ViewFile) {
        if (file != null) {
            this.toggleSelectionEvent.emit(file);
        }
    }

    // Source: https://stackoverflow.com/a/7557433
    private static isElementInViewport (el) {
        const rect = el.getBoundingClientRect();
        return (
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) && /*or $(window).height() */
            rect.right <= (window.innerWidth || document.documentElement.clientWidth) /*or $(window).width() */
        );
    }
}

export enum FileAction {
    QUEUE,
    STOP,
    EXTRACT,
    DELETE_LOCAL,
    DELETE_REMOTE,
    VALIDATE
}
