import {BehaviorSubject, of, throwError} from "rxjs";

import * as Immutable from "immutable";

import {FileListComponent} from "../../../../pages/files/file-list.component";
import {FileComponent} from "../../../../pages/files/file.component";
import {ViewFile} from "../../../../services/files/view-file";
import {ViewFileOptions} from "../../../../services/files/view-file-options";
import {WebReaction} from "../../../../services/utils/rest.service";


class MockLoggerService {
    info = jasmine.createSpy("info");
    debug = jasmine.createSpy("debug");
    error = jasmine.createSpy("error");
}

class MockViewFileService {
    private _filteredFiles = new BehaviorSubject(Immutable.List<ViewFile>());
    stop = jasmine.createSpy("stop").and.returnValue(
        of(new WebReaction(false, null, "Operation timed out"))
    );
    deleteLocal = jasmine.createSpy("deleteLocal").and.returnValue(
        of(new WebReaction(true, "ok", null))
    );

    get filteredFiles() {
        return this._filteredFiles.asObservable();
    }
}

class MockViewFileOptionsService {
    options = new BehaviorSubject(new ViewFileOptions({
        showDetails: false,
        sortMethod: ViewFileOptions.SortMethod.STATUS,
        selectedStatusFilter: null,
        nameFilter: null,
        pinFilter: false
    })).asObservable();
}

class MockFileSelectionService {
    selectedFileIds = new BehaviorSubject(Immutable.Set<string>()).asObservable();
    selectedFiles = new BehaviorSubject(Immutable.List<ViewFile>()).asObservable();
    areAllVisibleSelected = new BehaviorSubject(false).asObservable();
    setVisibleFiles = jasmine.createSpy("setVisibleFiles");
}

class MockChangeDetectorRef {
    markForCheck = jasmine.createSpy("markForCheck");
}

function createViewFile(props: any = {}): ViewFile {
    return new ViewFile({
        fileId: props.fileId || "file-1",
        name: props.name || "sample",
        isStoppable: props.isStoppable != null ? props.isStoppable : true
    });
}

describe("Testing file list component", () => {
    let component: FileListComponent;
    let mockViewFileService: MockViewFileService;
    let mockLogger: MockLoggerService;
    let mockFileComponent: FileComponent;

    beforeEach(() => {
        mockLogger = new MockLoggerService();
        mockViewFileService = new MockViewFileService();
        component = new FileListComponent(
            mockLogger as any,
            mockViewFileService as any,
            new MockViewFileOptionsService() as any,
            new MockFileSelectionService() as any,
            new MockChangeDetectorRef() as any
        );

        mockFileComponent = {
            file: createViewFile(),
            resetActiveAction: jasmine.createSpy("resetActiveAction")
        } as any;
        (component as any).fileComponents = {
            toArray: () => [mockFileComponent]
        };
    });

    it("should clear the stop loading state when the stop request fails", () => {
        component.onStop(createViewFile());

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalled();
    });

    it("should clear the stop loading state when the stop request succeeds", () => {
        mockViewFileService.stop.and.returnValue(
            of(new WebReaction(true, "ok", null))
        );

        component.onStop(createViewFile());

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalled();
    });

    it("should reset stop loading on the matching row when duplicate display names are re-instantiated with the same file id", () => {
        const matchingFile = createViewFile();
        const matchingFileComponent = {
            file: matchingFile,
            resetActiveAction: jasmine.createSpy("matchingResetActiveAction")
        } as any;
        const otherFileComponent = {
            file: createViewFile({
                fileId: "file-2",
                name: matchingFile.name,
                isStoppable: true
            }),
            resetActiveAction: jasmine.createSpy("otherResetActiveAction")
        } as any;
        (component as any).fileComponents = {
            toArray: () => [otherFileComponent, matchingFileComponent]
        };

        component.onStop(createViewFile({
            fileId: matchingFile.fileId,
            name: matchingFile.name,
            isStoppable: true
        }));

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(matchingFileComponent.resetActiveAction).toHaveBeenCalledTimes(1);
        expect(otherFileComponent.resetActiveAction).not.toHaveBeenCalled();
    });

    it("should clear the stop loading state when the stop request errors", () => {
        mockViewFileService.stop.and.returnValue(throwError("boom"));

        component.onStop(createViewFile());

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalled();
    });

    it("should clear the delete local loading state when the delete request succeeds", () => {
        component.onDeleteLocal(createViewFile());

        expect(mockViewFileService.deleteLocal).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalled();
    });

    it("should clear the delete local loading state when the delete request fails", () => {
        mockViewFileService.deleteLocal.and.returnValue(
            of(new WebReaction(false, null, "Operation timed out"))
        );

        component.onDeleteLocal(createViewFile());

        expect(mockViewFileService.deleteLocal).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalled();
    });
});
