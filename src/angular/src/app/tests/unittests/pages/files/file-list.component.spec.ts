import {BehaviorSubject, of, Subject, throwError} from "rxjs";

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
    private _pageSize = new BehaviorSubject(0);
    queue = jasmine.createSpy("queue").and.returnValue(of(new WebReaction(true, "ok", null)));
    stop = jasmine.createSpy("stop").and.returnValue(
        of(new WebReaction(false, null, "Operation timed out"))
    );
    extract = jasmine.createSpy("extract").and.returnValue(of(new WebReaction(true, "ok", null)));
    deleteLocal = jasmine.createSpy("deleteLocal").and.returnValue(
        of(new WebReaction(true, "ok", null))
    );
    deleteRemote = jasmine.createSpy("deleteRemote").and.returnValue(of(new WebReaction(true, "ok", null)));
    validate = jasmine.createSpy("validate").and.returnValue(of(new WebReaction(true, "ok", null)));
    retryMove = jasmine.createSpy("retryMove").and.returnValue(
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
    return new ViewFile(Object.assign({
        fileId: props.fileId || "file-1",
        name: props.name || "sample",
        isStoppable: props.isStoppable != null ? props.isStoppable : true
    }, props));
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

    it("should clear retry move loading on success and error", () => {
        const file = createViewFile();

        component.onRetryMove(file);
        expect(mockViewFileService.retryMove).toHaveBeenCalledWith(file);
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalledWith(file, FileAction.RETRY_MOVE);

        (mockFileComponent.resetActiveAction as jasmine.Spy).calls.reset();
        mockViewFileService.retryMove.and.returnValue(throwError("boom"));
        component.onRetryMove(file);
        expect(mockFileComponent.resetActiveAction).toHaveBeenCalledWith(file, FileAction.RETRY_MOVE);
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
        expect(component.PAGE_SIZES).toEqual([25, 50, 100, 500, 1000, 0]);
    });

    it("should forward the selected page size and treat All as zero", () => {
        component.onPageSizeChange("500");

        expect(mockViewFileService.setPageSize).toHaveBeenCalledWith(500);
        expect(component.pageSize).toBe(500);

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

    it("should keep the complete logical list while bounding the mounted large-list slice", () => {
        const files = Immutable.Range(0, 1000).map(index => createViewFile({
            fileId: `file-${index}`,
            name: `sample-${index}`
        })).toList();

        mockViewFileService.setFilteredFiles(files);
        (component as any).updateVisibleRange();

        expect(component.logicalFiles.size).toBe(1000);
        expect(component.visibleFiles.length).toBeGreaterThan(0);
        expect(component.visibleFiles.length).toBeLessThan(100);
        expect(component.renderedStartIndex).toBe(0);
        expect(component.renderedEndIndex).toBe(component.visibleFiles.length);
    });

    it("should advance the rendered logical range when the page scrolls", () => {
        const files = Immutable.Range(0, 1000).map(index => createViewFile({
            fileId: `file-${index}`,
            name: `sample-${index}`
        })).toList();

        mockViewFileService.setFilteredFiles(files);
        (component as any).updateVisibleRange();
        const initialStart = component.renderedStartIndex;

        component.onWindowScroll(5000);
        (component as any).updateVisibleRange();

        expect(component.renderedStartIndex).toBeGreaterThan(initialStart);
        expect(component.renderedEndIndex).toBeLessThanOrEqual(1000);
        expect(component.visibleFiles[0].fileId).toBe(`file-${component.renderedStartIndex}`);
    });

    it("should use measured variable row heights when calculating spacers", () => {
        const files = Immutable.Range(0, 1000).map(index => createViewFile({
            fileId: `file-${index}`,
            name: `sample-${index}`
        })).toList();

        mockViewFileService.setFilteredFiles(files);
        (component as any).updateVisibleRange();
        const before = component.bottomSpacerHeight;

        (component as any).updateRowHeight("file-0", 240);
        (component as any).updateVisibleRange();

        expect(component.topSpacerHeight).toBe(0);
        expect(component.bottomSpacerHeight).toBeGreaterThan(before);
    });

    it("should retain a hard mounted-row bound even when measured rows are unusually short", () => {
        const files = Immutable.Range(0, 1000).map(index => createViewFile({
            fileId: `short-${index}`,
            name: `short-${index}`
        })).toList();

        mockViewFileService.setFilteredFiles(files);
        files.forEach(file => (component as any).updateRowHeight(file.fileId, 1));
        (component as any).updateVisibleRange();

        expect(component.logicalFiles.size).toBe(1000);
        expect(component.visibleFiles.length).toBeLessThanOrEqual(80);
    });

    it("should render small page sizes without dropping logical rows", () => {
        [25, 50, 100].forEach(size => {
            const files = Immutable.Range(0, size).map(index => createViewFile({
                fileId: `file-${size}-${index}`,
                name: `sample-${size}-${index}`
            })).toList();

            mockViewFileService.setFilteredFiles(files);
            (component as any).updateVisibleRange();

            expect(component.logicalFiles.size).toBe(size);
            expect(component.visibleFiles.length).toBe(size);
            expect(component.renderedStartIndex).toBe(0);
            expect(component.renderedEndIndex).toBe(size);
        });
    });

    it("should rebuild the window when filtering or sorting changes the logical page", () => {
        const initialFiles = Immutable.Range(0, 1000).map(index => createViewFile({
            fileId: `file-${index}`,
            name: `sample-${index}`
        })).toList();
        mockViewFileService.setFilteredFiles(initialFiles);
        component.onWindowScroll(4000);
        (component as any).updateVisibleRange();
        expect(component.renderedStartIndex).toBeGreaterThan(0);

        const filteredFiles = Immutable.Range(0, 25).map(index => createViewFile({
            fileId: `filtered-${index}`,
            name: `filtered-${index}`
        })).toList();
        mockViewFileService.setFilteredFiles(filteredFiles);
        (component as any).updateVisibleRange();

        expect(component.logicalFiles.size).toBe(25);
        expect(component.visibleFiles.length).toBe(25);
        expect(component.renderedStartIndex).toBe(0);
        expect(component.renderedEndIndex).toBe(25);
    });

    it("should retain an in-flight action lock across row remounts and clear it only on completion", () => {
        const completion = new Subject<WebReaction>();
        mockViewFileService.stop.and.returnValue(completion.asObservable());
        const file = createViewFile({fileId: "in-flight", isStoppable: true});

        component.onStop(file);
        expect(component.activeActionFor(file)).toBe(FileAction.STOP);

        // The old row may be destroyed by windowing; a remounted row reads the
        // parent-owned action state and must not issue a duplicate request.
        (component as any).fileComponents = {toArray: () => []};
        expect(component.activeActionFor(file)).toBe(FileAction.STOP);
        component.onStop(file);
        expect(mockViewFileService.stop).toHaveBeenCalledTimes(1);

        completion.next(new WebReaction(true, "ok", null));
        completion.complete();
        expect(component.activeActionFor(file)).toBeNull();
    });

    it("should keep status-driven actions locked after HTTP acknowledgement until the model transition", () => {
        const cases = [
            {
                action: FileAction.QUEUE,
                service: "queue",
                invoke: (file: ViewFile) => component.onQueue(file),
                unrelatedFile: (file: ViewFile) => new ViewFile(file.set("status", ViewFile.Status.DOWNLOADED)),
                nextFile: (file: ViewFile) => new ViewFile(file.set("status", ViewFile.Status.QUEUED))
            },
            {
                action: FileAction.EXTRACT,
                service: "extract",
                invoke: (file: ViewFile) => component.onExtract(file),
                unrelatedFile: (file: ViewFile) => new ViewFile(file.set("status", ViewFile.Status.DOWNLOADED)),
                nextFile: (file: ViewFile) => new ViewFile(file.set("status", ViewFile.Status.EXTRACTING))
            },
            {
                action: FileAction.DELETE_REMOTE,
                service: "deleteRemote",
                invoke: (file: ViewFile) => component.onDeleteRemote(file),
                unrelatedFile: (file: ViewFile) => new ViewFile(file.set("status", ViewFile.Status.DOWNLOADED)),
                nextFile: (file: ViewFile) => new ViewFile(file.set("isRemotelyDeletable", false))
            },
            {
                action: FileAction.VALIDATE,
                service: "validate",
                invoke: (file: ViewFile) => component.onValidate(file),
                unrelatedFile: (file: ViewFile) => new ViewFile(file.set("status", ViewFile.Status.DOWNLOADED)),
                nextFile: (file: ViewFile) => new ViewFile(file.set("status", ViewFile.Status.VALIDATING))
            }
        ];

        cases.forEach(({action, service, invoke, unrelatedFile, nextFile}, index) => {
            const completion = new Subject<WebReaction>();
            (mockViewFileService as any)[service].and.returnValue(completion.asObservable());
            const file = createViewFile({
                fileId: `status-driven-${index}`,
                isRemotelyDeletable: action === FileAction.DELETE_REMOTE
            });
            mockViewFileService.setFilteredFiles(Immutable.List([file]));

            invoke(file);
            expect(component.activeActionFor(file)).toBe(action);

            completion.next(new WebReaction(true, "accepted", null));
            expect(component.activeActionFor(file)).toBe(action);
            completion.complete();
            expect(component.activeActionFor(file)).toBe(action);

            const unrelated = unrelatedFile(file);
            mockViewFileService.setFilteredFiles(Immutable.List([unrelated]));
            expect(component.activeActionFor(unrelated)).toBe(action);

            const transitionedFile = nextFile(file);
            mockViewFileService.setFilteredFiles(Immutable.List([transitionedFile]));
            expect(component.activeActionFor(transitionedFile)).toBeNull();
        });
    });

    it("should require a genuine status transition for same-status model emissions", () => {
        const cases = [
            {
                action: FileAction.QUEUE,
                service: "queue",
                status: ViewFile.Status.QUEUED,
                sameStatusField: "downloadingSpeed",
                nextStatus: ViewFile.Status.DOWNLOADING,
                invoke: (file: ViewFile) => component.onQueue(file)
            },
            {
                action: FileAction.EXTRACT,
                service: "extract",
                status: ViewFile.Status.EXTRACTED,
                sameStatusField: "localSize",
                nextStatus: ViewFile.Status.EXTRACTING,
                invoke: (file: ViewFile) => component.onExtract(file)
            },
            {
                action: FileAction.VALIDATE,
                service: "validate",
                status: ViewFile.Status.VALIDATED,
                sameStatusField: "validationProgress",
                nextStatus: ViewFile.Status.VALIDATING,
                invoke: (file: ViewFile) => component.onValidate(file)
            },
            {
                action: FileAction.VALIDATE,
                service: "validate",
                status: ViewFile.Status.CORRUPT,
                sameStatusField: "validationError",
                nextStatus: ViewFile.Status.VALIDATING,
                invoke: (file: ViewFile) => component.onValidate(file)
            }
        ];

        cases.forEach(({action, service, status, sameStatusField, nextStatus, invoke}, index) => {
            const completion = new Subject<WebReaction>();
            (mockViewFileService as any)[service].and.returnValue(completion.asObservable());
            const file = createViewFile({
                fileId: `same-status-${index}`,
                status,
                isQueueable: action === FileAction.QUEUE,
                isExtractable: action === FileAction.EXTRACT,
                isArchive: action === FileAction.EXTRACT,
                isValidatable: action === FileAction.VALIDATE
            });
            mockViewFileService.setFilteredFiles(Immutable.List([file]));

            invoke(file);
            completion.next(new WebReaction(true, "accepted", null));
            completion.complete();

            const sameStatusFile = new ViewFile(file.set(sameStatusField, "updated"));
            mockViewFileService.setFilteredFiles(Immutable.List([sameStatusFile]));
            expect(component.activeActionFor(sameStatusFile)).toBe(action);

            const transitionedFile = new ViewFile(sameStatusFile.set("status", nextStatus));
            mockViewFileService.setFilteredFiles(Immutable.List([transitionedFile]));
            expect(component.activeActionFor(transitionedFile)).toBeNull();
        });
    });

    it("should clear a status-driven action on completion without an acknowledgement", () => {
        const completion = new Subject<WebReaction>();
        mockViewFileService.queue.and.returnValue(completion.asObservable());
        const file = createViewFile({fileId: "completion-only", isQueueable: true});
        mockViewFileService.setFilteredFiles(Immutable.List([file]));

        component.onQueue(file);
        expect(component.activeActionFor(file)).toBe(FileAction.QUEUE);

        completion.complete();

        expect(component.activeActionFor(file)).toBeNull();
    });

    it("should clear a response-driven action on completion without an acknowledgement", () => {
        const completion = new Subject<WebReaction>();
        mockViewFileService.stop.and.returnValue(completion.asObservable());
        const file = createViewFile({fileId: "response-completion-only", isStoppable: true});
        mockViewFileService.setFilteredFiles(Immutable.List([file]));

        component.onStop(file);
        expect(component.activeActionFor(file)).toBe(FileAction.STOP);

        completion.complete();

        expect(component.activeActionFor(file)).toBeNull();
    });

    it("should clear an action error after the row remounts and reject duplicate requests", () => {
        const request = new Subject<WebReaction>();
        mockViewFileService.queue.and.returnValue(request.asObservable());
        const file = createViewFile({fileId: "remount-error", isQueueable: true});
        mockViewFileService.setFilteredFiles(Immutable.List([file]));

        component.onQueue(file);
        (component as any).fileComponents = {toArray: () => []};
        component.onQueue(file);
        expect(mockViewFileService.queue).toHaveBeenCalledTimes(1);

        request.error(new Error("request failed"));

        expect(component.activeActionFor(file)).toBeNull();
    });

    it("should clear a synchronous action factory failure and allow a retry", () => {
        const file = createViewFile({fileId: "sync-failure", isQueueable: true});
        mockViewFileService.queue.and.callFake(() => {
            throw new Error("factory failed");
        });

        component.onQueue(file);
        expect(component.activeActionFor(file)).toBeNull();

        component.onQueue(file);
        expect(mockViewFileService.queue).toHaveBeenCalledTimes(2);
        expect(component.activeActionFor(file)).toBeNull();
    });

    it("should preserve the queue action enum value when exposing the parent lock", () => {
        const completion = new Subject<WebReaction>();
        mockViewFileService.queue.and.returnValue(completion.asObservable());
        const file = createViewFile({fileId: "queue-lock", isQueueable: true});

        component.onQueue(file);

        expect(component.activeActionFor(file)).toBe(FileAction.QUEUE);
        expect(mockViewFileService.queue).toHaveBeenCalledTimes(1);
        completion.next(new WebReaction(true, "ok", null));
        expect(component.activeActionFor(file)).toBe(FileAction.QUEUE);

        const queuedFile = new ViewFile(file.set("status", ViewFile.Status.QUEUED));
        mockViewFileService.setFilteredFiles(Immutable.List([queuedFile]));
        expect(component.activeActionFor(queuedFile)).toBeNull();
    });

    it("should reuse the cached height index for repeated unchanged range updates", () => {
        const files = Immutable.Range(0, 1000).map(index => createViewFile({
            fileId: `cached-${index}`,
            name: `cached-${index}`
        })).toList();
        mockViewFileService.setFilteredFiles(files);
        (component as any).updateVisibleRange();
        const rebuilds = (component as any)._heightIndexRebuilds;

        for (let index = 0; index < 8; index++) {
            component.onWindowScroll(index * 500);
            (component as any).updateVisibleRange();
        }

        expect((component as any)._heightIndexRebuilds).toBe(rebuilds);
    });
});
