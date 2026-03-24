import {ChangeDetectorRef, SimpleChange} from "@angular/core";

import {Modal} from "ngx-modialog/plugins/bootstrap";

import {FileAction, FileComponent} from "../../../../pages/files/file.component";
import {ViewFile} from "../../../../services/files/view-file";
import {ModalAccessibilityService} from "../../../../services/utils/modal-accessibility.service";


class MockModal {}

class MockModalAccessibilityService {}

class MockChangeDetectorRef {
    markForCheck = jasmine.createSpy("markForCheck");
}

function createViewFile(props): ViewFile {
    return new ViewFile({
        fileId: props.fileId || "file-1",
        name: props.name || "sample",
        isArchive: props.isArchive || false,
        isQueueable: props.isQueueable || false,
        isStoppable: props.isStoppable || false,
        isExtractable: props.isExtractable || false,
        isLocallyDeletable: props.isLocallyDeletable || false,
        isRemotelyDeletable: props.isRemotelyDeletable || false,
        isValidatable: props.isValidatable || false
    });
}

describe("Testing file component", () => {
    let component: FileComponent;
    let changeDetector: MockChangeDetectorRef;

    beforeEach(() => {
        changeDetector = new MockChangeDetectorRef();
        component = new FileComponent(
            new MockModal() as Modal,
            new MockModalAccessibilityService() as ModalAccessibilityService,
            changeDetector as unknown as ChangeDetectorRef
        );
    });

    it("should return false for action predicates when file is missing", () => {
        component.file = null;

        expect(component.isQueueable()).toBe(false);
        expect(component.isStoppable()).toBe(false);
        expect(component.isExtractable()).toBe(false);
        expect(component.isLocallyDeletable()).toBe(false);
        expect(component.isRemotelyDeletable()).toBe(false);
        expect(component.isValidatable()).toBe(false);
    });

    it("should not emit queue when the file is not queueable", () => {
        const queueSpy = spyOn(component.queueEvent, "emit");
        component.file = createViewFile({isQueueable: false});

        component.onQueue(component.file);

        expect(component.activeAction).toBe(null);
        expect(queueSpy).not.toHaveBeenCalled();
    });

    it("should not open delete local confirmation when the file is not deletable", () => {
        component.file = createViewFile({isLocallyDeletable: false});
        const confirmSpy = spyOn(component, "showDeleteConfirmation");

        component.onDeleteLocal(component.file);

        expect(confirmSpy).not.toHaveBeenCalled();
        expect(component.activeAction).toBe(null);
    });

    it("should ignore selection toggle when file is null", () => {
        const toggleSpy = spyOn(component.toggleSelectionEvent, "emit");

        component.onToggleSelection(null);

        expect(toggleSpy).not.toHaveBeenCalled();
    });

    it("should ignore unrelated ngOnChanges payloads without crashing", () => {
        expect(() => component.ngOnChanges({
            showActions: new SimpleChange(true, false, false)
        })).not.toThrow();
    });

    it("should set the active action when queueing a queueable file", () => {
        const queueSpy = spyOn(component.queueEvent, "emit");
        component.file = createViewFile({isQueueable: true});

        component.onQueue(component.file);

        expect(component.activeAction).toBe(FileAction.QUEUE);
        expect(queueSpy).toHaveBeenCalledWith(component.file);
    });

    it("should not emit validate when the file is not validatable", () => {
        const validateSpy = spyOn(component.validateEvent, "emit");
        component.file = createViewFile({isValidatable: false});

        component.onValidate(component.file);

        expect(component.activeAction).toBe(null);
        expect(validateSpy).not.toHaveBeenCalled();
    });

    it("should set the active action when validating a validatable file", () => {
        const validateSpy = spyOn(component.validateEvent, "emit");
        component.file = createViewFile({isValidatable: true});

        component.onValidate(component.file);

        expect(component.activeAction).toBe(FileAction.VALIDATE);
        expect(validateSpy).toHaveBeenCalledWith(component.file);
    });

    it("should clear the active action when resetActiveAction is called", () => {
        component.activeAction = FileAction.STOP;

        component.resetActiveAction();

        expect(component.activeAction).toBe(null);
        expect(changeDetector.markForCheck).toHaveBeenCalled();
    });
});
