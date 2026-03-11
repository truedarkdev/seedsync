import {SimpleChange} from "@angular/core";

import {Modal} from "ngx-modialog/plugins/bootstrap";

import {FileAction, FileComponent} from "../../../../pages/files/file.component";
import {ViewFile} from "../../../../services/files/view-file";
import {ModalAccessibilityService} from "../../../../services/utils/modal-accessibility.service";


class MockModal {}

class MockModalAccessibilityService {}

function createViewFile(props): ViewFile {
    return new ViewFile({
        fileId: props.fileId || "file-1",
        name: props.name || "sample",
        isArchive: props.isArchive || false,
        isQueueable: props.isQueueable || false,
        isStoppable: props.isStoppable || false,
        isExtractable: props.isExtractable || false,
        isLocallyDeletable: props.isLocallyDeletable || false,
        isRemotelyDeletable: props.isRemotelyDeletable || false
    });
}

describe("Testing file component", () => {
    let component: FileComponent;

    beforeEach(() => {
        component = new FileComponent(
            new MockModal() as Modal,
            new MockModalAccessibilityService() as ModalAccessibilityService
        );
    });

    it("should return false for action predicates when file is missing", () => {
        component.file = null;

        expect(component.isQueueable()).toBe(false);
        expect(component.isStoppable()).toBe(false);
        expect(component.isExtractable()).toBe(false);
        expect(component.isLocallyDeletable()).toBe(false);
        expect(component.isRemotelyDeletable()).toBe(false);
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
});
