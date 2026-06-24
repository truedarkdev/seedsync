import {CommonModule} from "@angular/common";
import {ChangeDetectorRef, Directive, Input, Pipe, PipeTransform, SimpleChange} from "@angular/core";
import {ComponentFixture, TestBed} from "@angular/core/testing";
import {By} from "@angular/platform-browser";
import {of} from "rxjs";

import {Modal} from "../../../../services/utils/modal.service";

import {FileAction, FileComponent} from "../../../../pages/files/file.component";
import {ViewFile} from "../../../../services/files/view-file";
import {ModalAccessibilityService} from "../../../../services/utils/modal-accessibility.service";


class MockModal {}

class MockModalAccessibilityService {}

class MockChangeDetectorRef {
    markForCheck = jasmine.createSpy("markForCheck");
}

@Directive({
    selector: "[appClickStopPropagation]",
    standalone: false
})
class MockClickStopPropagationDirective {}

@Pipe({name: "capitalize", standalone: false})
class MockCapitalizePipe implements PipeTransform {
    transform(value: any): any {
        return value;
    }
}

@Pipe({name: "fileSize", standalone: false})
class MockFileSizePipe implements PipeTransform {
    transform(value: any): any {
        return value;
    }
}

@Pipe({name: "eta", standalone: false})
class MockEtaPipe implements PipeTransform {
    transform(value: any): any {
        return value;
    }
}

function createViewFile(props: any = {}): ViewFile {
    return new ViewFile({
        fileId: props.fileId || "file-1",
        name: props.name || "sample",
        status: props.status || ViewFile.Status.DEFAULT,
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
    let fixture: ComponentFixture<FileComponent>;

    beforeEach(() => {
        changeDetector = new MockChangeDetectorRef();
        component = new FileComponent(
            new MockModal() as Modal,
            new MockModalAccessibilityService() as ModalAccessibilityService,
            changeDetector as unknown as ChangeDetectorRef
        );
    });

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [
                FileComponent,
                MockClickStopPropagationDirective,
                MockCapitalizePipe,
                MockFileSizePipe,
                MockEtaPipe
            ],
            imports: [CommonModule],
            providers: [
                {provide: Modal, useClass: MockModal},
                {provide: ModalAccessibilityService, useClass: MockModalAccessibilityService},
                {provide: ChangeDetectorRef, useValue: changeDetector}
            ]
        });

        fixture = TestBed.createComponent(FileComponent);
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

    it("should clear the active action when ngOnChanges rebinding changes the file identity", () => {
        const oldFile = createViewFile({
            fileId: "file-1",
            name: "old",
            status: ViewFile.Status.DOWNLOADING,
            isStoppable: true
        });
        const newFile = createViewFile({
            fileId: "file-2",
            name: "new",
            status: ViewFile.Status.DOWNLOADING,
            isStoppable: true
        });
        component.file = newFile;
        component.activeAction = FileAction.STOP;

        component.ngOnChanges({
            file: new SimpleChange(oldFile, newFile, false)
        });

        expect(component.activeAction).toBe(null);
        expect(changeDetector.markForCheck).toHaveBeenCalled();
    });

    it("should clear the active remote delete action when remote deletability drops without a status change", () => {
        const oldFile = createViewFile({
            status: ViewFile.Status.DOWNLOADED,
            isRemotelyDeletable: true
        });
        const newFile = createViewFile({
            fileId: oldFile.fileId,
            name: oldFile.name,
            status: ViewFile.Status.DOWNLOADED,
            isRemotelyDeletable: false
        });
        component.file = newFile;
        component.activeAction = FileAction.DELETE_REMOTE;

        component.ngOnChanges({
            file: new SimpleChange(oldFile, newFile, false)
        });

        expect(component.activeAction).toBe(null);
        expect(changeDetector.markForCheck).toHaveBeenCalled();
    });

    it("should clear the active local delete action when local deletability drops without a status change", () => {
        const oldFile = createViewFile({
            status: ViewFile.Status.DOWNLOADED,
            isLocallyDeletable: true
        });
        const newFile = createViewFile({
            fileId: oldFile.fileId,
            name: oldFile.name,
            status: ViewFile.Status.DOWNLOADED,
            isLocallyDeletable: false
        });
        component.file = newFile;
        component.activeAction = FileAction.DELETE_LOCAL;

        component.ngOnChanges({
            file: new SimpleChange(oldFile, newFile, false)
        });

        expect(component.activeAction).toBe(null);
        expect(changeDetector.markForCheck).toHaveBeenCalled();
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

    it("should clear the active action when resetActiveAction is called for the same file and action", () => {
        const file = createViewFile();
        component.file = file;
        component.activeAction = FileAction.STOP;

        component.resetActiveAction(file, FileAction.STOP);

        expect(component.activeAction).toBe(null);
        expect(changeDetector.markForCheck).toHaveBeenCalled();
    });

    it("should ignore stale reset callbacks after the component is rebound to another file", () => {
        const oldFile = createViewFile({fileId: "file-1", name: "old"});
        component.file = createViewFile({fileId: "file-2", name: "new"});
        component.activeAction = FileAction.STOP;

        component.resetActiveAction(oldFile, FileAction.STOP);

        expect(component.activeAction).toBe(FileAction.STOP);
        expect(changeDetector.markForCheck).not.toHaveBeenCalled();
    });

    it("should ignore stale reset callbacks when the file keeps the same identity but the action changes", () => {
        const file = createViewFile();
        component.file = file;
        component.activeAction = FileAction.DELETE_LOCAL;

        component.resetActiveAction(file, FileAction.STOP);

        expect(component.activeAction).toBe(FileAction.DELETE_LOCAL);
        expect(changeDetector.markForCheck).not.toHaveBeenCalled();
    });

    it("should render the stop action as a disabled button for stopped rows", () => {
        const stopSpy = spyOn(fixture.componentInstance.stopEvent, "emit");
        fixture.componentInstance.file = createViewFile({
            status: ViewFile.Status.STOPPED,
            isStoppable: false
        });
        fixture.componentInstance.showActions = true;
        fixture.componentInstance.options = of(null) as any;

        fixture.detectChanges();

        const stopButton = fixture.debugElement.query(By.css("button.stop-action")).nativeElement as HTMLButtonElement;
        stopButton.click();

        expect(stopButton.disabled).toBe(true);
        expect(stopButton.getAttribute("aria-disabled")).toBe("true");
        expect(stopSpy).not.toHaveBeenCalled();
    });

    it("should keep the stop action enabled for stoppable rows", () => {
        const stopSpy = spyOn(fixture.componentInstance.stopEvent, "emit");
        fixture.componentInstance.file = createViewFile({
            status: ViewFile.Status.DOWNLOADING,
            isStoppable: true
        });
        fixture.componentInstance.showActions = true;
        fixture.componentInstance.options = of(null) as any;

        fixture.detectChanges();

        const stopButton = fixture.debugElement.query(By.css("button.stop-action")).nativeElement as HTMLButtonElement;
        stopButton.click();

        expect(stopButton.disabled).toBe(false);
        expect(fixture.componentInstance.activeAction).toBe(FileAction.STOP);
        expect(stopSpy).toHaveBeenCalledWith(fixture.componentInstance.file);
    });

    it("should transition the stop action from enabled to loading and then settle disabled for a stopped row", () => {
        const stopSpy = spyOn(fixture.componentInstance.stopEvent, "emit");
        const downloadingFile = createViewFile({
            status: ViewFile.Status.DOWNLOADING,
            isStoppable: true
        });
        fixture.componentInstance.file = downloadingFile;
        fixture.componentInstance.showActions = true;
        fixture.componentInstance.options = of(null) as any;

        fixture.detectChanges();

        const stopButton = fixture.debugElement.query(By.css("button.stop-action")).nativeElement as HTMLButtonElement;

        expect(stopButton.disabled).toBe(false);

        stopButton.click();
        fixture.detectChanges();

        expect(fixture.componentInstance.activeAction).toBe(FileAction.STOP);
        expect(stopButton.disabled).toBe(true);
        expect(stopButton.classList.contains("loading")).toBe(true);
        expect(stopSpy).toHaveBeenCalledWith(downloadingFile);

        fixture.componentInstance.file = createViewFile({
            fileId: downloadingFile.fileId,
            name: downloadingFile.name,
            status: ViewFile.Status.STOPPED,
            isStoppable: false
        });
        fixture.componentInstance.ngOnChanges({
            file: new SimpleChange(downloadingFile, fixture.componentInstance.file, false)
        });
        fixture.detectChanges();

        expect(fixture.componentInstance.activeAction).toBe(null);
        expect(stopButton.disabled).toBe(true);
        expect(stopButton.classList.contains("loading")).toBe(false);
        expect(stopButton.getAttribute("aria-disabled")).toBe("true");
    });
});
