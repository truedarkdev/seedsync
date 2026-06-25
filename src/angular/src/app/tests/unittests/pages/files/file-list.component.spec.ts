import {BehaviorSubject, of, throwError} from "rxjs";

import * as Immutable from "immutable";

import {FileListComponent} from "../../../../pages/files/file-list.component";
import {FileAction, FileComponent} from "../../../../pages/files/file.component";
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
    private _totalFilteredCount = new BehaviorSubject(0);
    private _currentPage = new BehaviorSubject(0);
    private _pageSize = new BehaviorSubject(50);
    stop = jasmine.createSpy("stop").and.returnValue(
        of(new WebReaction(false, null, "Operation timed out"))
    );
    deleteLocal = jasmine.createSpy("deleteLocal").and.returnValue(
        of(new WebReaction(true, "ok", null))
    );
    setPageSize = jasmine.createSpy("setPageSize");
    prevPage = jasmine.createSpy("prevPage");
    nextPage = jasmine.createSpy("nextPage");

    get filteredFiles() {
        return this._filteredFiles.asObservable();
    }

    get totalFilteredCount() {
        return this._totalFilteredCount.asObservable();
    }

    get currentPage() {
        return this._currentPage.asObservable();
    }

    get pageSize() {
        return this._pageSize.asObservable();
    }

    setFilteredFiles(files: Immutable.List<ViewFile>) {
        this._filteredFiles.next(files);
        this._totalFilteredCount.next(files.size);
    }
}

class MockViewFileOptionsService {
    options = new BehaviorSubject(new ViewFileOptions({
        showDetails: false,
        sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
        selectedStatusFilter: null,
        nameFilter: null,
        pinFilter: false
    })).asObservable();
    setSortMethod = jasmine.createSpy("setSortMethod");
}

class MockFileSelectionService {
    selectedFileIds = new BehaviorSubject(Immutable.Set<string>()).asObservable();
    selectedFiles = new BehaviorSubject(Immutable.List<ViewFile>()).asObservable();
    areAllVisibleSelected = new BehaviorSubject(false).asObservable();
    setVisibleFiles = jasmine.createSpy("setVisibleFiles");
    setAllVisibleSelected = jasmine.createSpy("setAllVisibleSelected");
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
    let mockViewFileOptionsService: MockViewFileOptionsService;
    let mockLogger: MockLoggerService;
    let mockFileComponent: FileComponent;

    beforeEach(() => {
        mockLogger = new MockLoggerService();
        mockViewFileService = new MockViewFileService();
        mockViewFileOptionsService = new MockViewFileOptionsService();
        component = new FileListComponent(
            mockLogger as any,
            mockViewFileService as any,
            mockViewFileOptionsService as any,
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
        const file = createViewFile();

        component.onStop(file);

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalledWith(file, FileAction.STOP);
    });

    it("should clear the stop loading state when the stop request succeeds", () => {
        mockViewFileService.stop.and.returnValue(
            of(new WebReaction(true, "ok", null))
        );

        const file = createViewFile();

        component.onStop(file);

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalledWith(file, FileAction.STOP);
    });

    it("should reset stop loading on the matching row when duplicate display names are re-instantiated with the same file id", () => {
        const matchingFile = createViewFile();
        const requestedFile = createViewFile({
            fileId: matchingFile.fileId,
            name: matchingFile.name,
            isStoppable: true
        });
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

        component.onStop(requestedFile);

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(matchingFileComponent.resetActiveAction).toHaveBeenCalledWith(requestedFile, FileAction.STOP);
        expect(otherFileComponent.resetActiveAction).not.toHaveBeenCalled();
    });

    it("should clear the stop loading state when the stop request errors", () => {
        mockViewFileService.stop.and.returnValue(throwError("boom"));

        const file = createViewFile();

        component.onStop(file);

        expect(mockViewFileService.stop).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalledWith(file, FileAction.STOP);
    });

    it("should clear the delete local loading state when the delete request succeeds", () => {
        const file = createViewFile();

        component.onDeleteLocal(file);

        expect(mockViewFileService.deleteLocal).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalledWith(file, FileAction.DELETE_LOCAL);
    });

    it("should clear the delete local loading state when the delete request fails", () => {
        mockViewFileService.deleteLocal.and.returnValue(
            of(new WebReaction(false, null, "Operation timed out"))
        );

        const file = createViewFile();

        component.onDeleteLocal(file);

        expect(mockViewFileService.deleteLocal).toHaveBeenCalled();
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalledWith(file, FileAction.DELETE_LOCAL);
    });

    it("should switch Smart Status to Status Reverse when the status header is clicked", () => {
        component.onStatusSort(ViewFileOptions.SortMethod.SMART_STATUS);

        expect(mockViewFileOptionsService.setSortMethod).toHaveBeenCalledWith(
            ViewFileOptions.SortMethod.STATUS_DESC
        );
    });

    it("should switch legacy Status to Status Reverse when the status header is clicked", () => {
        component.onStatusSort(ViewFileOptions.SortMethod.STATUS);

        expect(mockViewFileOptionsService.setSortMethod).toHaveBeenCalledWith(
            ViewFileOptions.SortMethod.STATUS_DESC
        );
    });

    it("should switch Status Reverse back to Smart Status when the status header is clicked", () => {
        component.onStatusSort(ViewFileOptions.SortMethod.STATUS_DESC);

        expect(mockViewFileOptionsService.setSortMethod).toHaveBeenCalledWith(
            ViewFileOptions.SortMethod.SMART_STATUS
        );
    });

    it("should expose all page size choices including All", () => {
        expect(component.PAGE_SIZES).toEqual([25, 50, 100, 1000, 0]);
    });

    it("should forward the selected page size and treat All as zero", () => {
        component.onPageSizeChange("1000");

        expect(mockViewFileService.setPageSize).toHaveBeenCalledWith(1000);
        expect(component.pageSize).toBe(1000);

        component.onPageSizeChange("0");

        expect(mockViewFileService.setPageSize).toHaveBeenCalledWith(0);
        expect(component.pageSize).toBe(0);
    });

    it("should report the full range when All is selected", () => {
        component.pageSize = 0;
        component.totalCount = 17;
        component.currentPage = 3;
        component.totalPages = 1;

        expect(component.pageStart).toBe(1);
        expect(component.pageEnd).toBe(17);
        expect(component.totalPages).toBe(1);
    });
});
