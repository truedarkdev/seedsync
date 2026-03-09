import {ComponentFixture, TestBed} from "@angular/core/testing";

import * as Immutable from "immutable";

import {Modal} from "ngx-modialog/plugins/bootstrap";

import {BulkActionsBarComponent} from "../../../../pages/files/bulk-actions-bar.component";
import {ViewFile} from "../../../../services/files/view-file";
import {BulkCommandService} from "../../../../services/server/bulk-command.service";
import {FileSelectionService} from "../../../../services/files/file-selection.service";
import {LoggerService} from "../../../../services/utils/logger.service";
import {Observable} from "rxjs/Observable";
import "rxjs/add/observable/of";
import {WebReaction} from "../../../../services/utils/rest.service";


class MockDialogBuilder {
    title() { return this; }
    okBtn() { return this; }
    okBtnClass() { return this; }
    cancelBtn() { return this; }
    cancelBtnClass() { return this; }
    isBlocking() { return this; }
    showClose() { return this; }
    body() { return this; }
    open() {
        return Promise.resolve({
            result: Promise.resolve()
        });
    }
}

class MockModal {
    confirm() {
        return new MockDialogBuilder();
    }
}

class MockBulkCommandService {
    deleteLocal = jasmine.createSpy("deleteLocal").and.returnValue(
        Observable.of(new WebReaction(true, "ok", null))
    );
}

class MockFileSelectionService {
    clear = jasmine.createSpy("clear");
}

describe("Testing bulk actions bar component", () => {
    let component: BulkActionsBarComponent;
    let fixture: ComponentFixture<BulkActionsBarComponent>;
    let bulkCommandService: MockBulkCommandService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [
                BulkActionsBarComponent
            ],
            providers: [
                LoggerService,
                {provide: Modal, useClass: MockModal},
                {provide: BulkCommandService, useClass: MockBulkCommandService},
                {provide: FileSelectionService, useClass: MockFileSelectionService}
            ]
        });

        fixture = TestBed.createComponent(BulkActionsBarComponent);
        component = fixture.componentInstance;
        bulkCommandService = TestBed.get(BulkCommandService);
    });

    function createViewFile(props): ViewFile {
        return new ViewFile({
            fileId: props.fileId,
            name: props.name,
            isArchive: props.isArchive,
            isQueueable: props.isQueueable,
            isStoppable: props.isStoppable,
            isExtractable: props.isExtractable,
            isLocallyDeletable: props.isLocallyDeletable,
            isRemotelyDeletable: props.isRemotelyDeletable
        });
    }

    it("should disable queue when any selected file is not queueable", () => {
        component.selectedFiles = Immutable.List<ViewFile>([
            createViewFile({
                fileId: "[\"movies\",\"one\"]",
                name: "one",
                isArchive: false,
                isQueueable: true,
                isStoppable: false,
                isExtractable: false,
                isLocallyDeletable: true,
                isRemotelyDeletable: true
            }),
            createViewFile({
                fileId: "[\"tv\",\"two\"]",
                name: "two",
                isArchive: false,
                isQueueable: false,
                isStoppable: false,
                isExtractable: false,
                isLocallyDeletable: true,
                isRemotelyDeletable: true
            })
        ]);

        expect(component.isQueueable()).toBe(false);
    });

    it("should send all selected names for bulk delete local after confirmation", async () => {
        component.selectedFiles = Immutable.List<ViewFile>([
            createViewFile({
                fileId: "[\"movies\",\"one\"]",
                name: "one",
                isArchive: false,
                isQueueable: true,
                isStoppable: false,
                isExtractable: false,
                isLocallyDeletable: true,
                isRemotelyDeletable: true
            }),
            createViewFile({
                fileId: "[\"tv\",\"two\"]",
                name: "two",
                isArchive: false,
                isQueueable: true,
                isStoppable: false,
                isExtractable: false,
                isLocallyDeletable: true,
                isRemotelyDeletable: true
            })
        ]);

        component.deleteLocal();
        await Promise.resolve();
        await Promise.resolve();

        expect(bulkCommandService.deleteLocal).toHaveBeenCalledWith([
            {file_id: "[\"movies\",\"one\"]", name: "one"},
            {file_id: "[\"tv\",\"two\"]", name: "two"}
        ]);
    });
});
