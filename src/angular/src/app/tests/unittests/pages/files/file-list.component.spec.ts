import {BehaviorSubject, Observable} from "rxjs/Rx";

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
        Observable.of(new WebReaction(false, null, "Operation timed out"))
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

function createViewFile(): ViewFile {
    return new ViewFile({
        fileId: "file-1",
        name: "sample",
        isStoppable: true
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

    it("should keep the stop loading state when the stop request succeeds", () => {
        mockViewFileService.stop.and.returnValue(
            Observable.of(new WebReaction(true, "ok", null))
        );

        component.onStop(createViewFile());

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).not.toHaveBeenCalled();
    });

    it("should clear the stop loading state when the stop request errors", () => {
        mockViewFileService.stop.and.returnValue(Observable.throw("boom"));

        component.onStop(createViewFile());

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalled();
    });
});
