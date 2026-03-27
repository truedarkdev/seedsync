import {Injectable} from "@angular/core";
import {Observable} from "rxjs/Observable";
import {BehaviorSubject} from "rxjs/Rx";

import * as Immutable from "immutable";

import {LoggerService} from "../utils/logger.service";
import {ModelFile} from "./model-file";
import {ModelFileService} from "./model-file.service";
import {ViewFile} from "./view-file";
import {MOCK_MODEL_FILES} from "./mock-model-files";
import {StreamServiceRegistry} from "../base/stream-service.registry";
import {WebReaction} from "../utils/rest.service";
import {FileSelectionService} from "./file-selection.service";


/**
 * Interface defining filtering criteria for view files
 */
export interface ViewFileFilterCriteria {
    meetsCriteria(viewFile: ViewFile): boolean;
}


/**
 * Interface for sorting view files
 */
export interface ViewFileComparator {
    // noinspection TsLint
    (a: ViewFile, b: ViewFile): number;
}


/**
 * ViewFileService class provides the store of view files.
 * It implements the observable service pattern to push updates
 * as they become available.
 *
 * The view model needs to be ordered and have fast lookup/update.
 * Unfortunately, there exists no immutable SortedMap structure.
 * This class stores the following data structures:
 *    1. files: List(ViewFile)
 *              ViewFiles sorted in the display order
 *    2. indices: Map(name, index)
 *                Maps name to its index in sortedList
 * The runtime complexity of operations is:
 *    1. Update w/o state change:
 *          O(1) to find index and update the sorted list
 *    2. Updates w/ state change:
 *          O(1) to find index and update the sorted list
 *          O(n log n) to sort list (might be faster since
 *                     list is mostly sorted already??)
 *          O(n) to update indexMap
 *    3. Add:
 *          O(1) to add to list
 *          O(n log n) to sort list (might be faster since
 *                     list is mostly sorted already??)
 *          O(n) to update indexMap
 *    4. Remove:
 *          O(n) to remove from sorted list
 *          O(n) to update indexMap
 *
 * Filtering:
 *      This service also supports providing a filtered list of view files.
 *      The strategy of using pipes to filter at the component level is not
 *      recommended by Angular: https://angular.io/guide/pipes#appendix-no
 *      -filterpipe-or-orderbypipe
 *      Instead, we provide a separate filtered observer.
 *      Filtering is controlled via a single filter criteria. Advanced filters
 *      need to be built outside the service (see ViewFileFilterService)
 */
@Injectable()
export class ViewFileService {

    private readonly USE_MOCK_MODEL = false;

    private modelFileService: ModelFileService;

    private _files: Immutable.List<ViewFile> = Immutable.List([]);
    private _filesSubject: BehaviorSubject<Immutable.List<ViewFile>> = new BehaviorSubject(this._files);
    private _filteredFilesSubject: BehaviorSubject<Immutable.List<ViewFile>> = new BehaviorSubject(this._files);
    private _indices: Map<string, number> = new Map<string, number>();
    private _pageSize = 50;
    private _currentPage = 0;
    private _totalFilteredCountSubject: BehaviorSubject<number> = new BehaviorSubject(0);
    private _pageSizeSubject: BehaviorSubject<number> = new BehaviorSubject(this._pageSize);
    private _currentPageSubject: BehaviorSubject<number> = new BehaviorSubject(this._currentPage);

    private _prevModelFiles: Immutable.Map<string, ModelFile> = Immutable.Map<string, ModelFile>();

    private _filterCriteria: ViewFileFilterCriteria = null;
    private _sortComparator: ViewFileComparator = null;

    constructor(private _logger: LoggerService,
                private _streamServiceRegistry: StreamServiceRegistry,
                private _fileSelectionService: FileSelectionService) {
        this.modelFileService = _streamServiceRegistry.modelFileService;
        const _viewFileService = this;

        if (!this.USE_MOCK_MODEL) {
            this.modelFileService.files.subscribe({
                next: modelFiles => {
                    let t0 = performance.now();
                    _viewFileService.buildViewFromModelFiles(modelFiles);
                    let t1 = performance.now();
                    this._logger.debug("ViewFile creation took", (t1 - t0).toFixed(0), "ms");
                }
            });
        } else {
            // For layout/style testing
            this.buildViewFromModelFiles(MOCK_MODEL_FILES);
        }
    }

    private static getViewFileKey(file: ViewFile): string {
        return file.fileId || file.name;
    }

    private static getModelFileKey(file: ModelFile): string {
        return file.file_id || file.name;
    }

    private buildViewFromModelFiles(modelFiles: Immutable.Map<string, ModelFile>) {
        this._logger.debug("Received next model files");

        // Diff the previous domain model with the current domain model, then apply
        // those changes to the view model
        // This is a roughly O(2N) operation on every update, so won't scale well
        // But should be fine for small models
        // A more scalable solution would be to subscribe to domain model updates
        let newViewFiles = this._files;

        const addedNames: string[] = [];
        const removedNames: string[] = [];
        const updatedNames: string[] = [];
        // Loop through old model to find deletions
        this._prevModelFiles.keySeq().forEach(
            name => {
                if (!modelFiles.has(name)) { removedNames.push(name); }
            }
        );
        // Loop through new model to find additions and updates
        modelFiles.keySeq().forEach(
            name => {
                if (!this._prevModelFiles.has(name)) {
                    addedNames.push(name);
                } else if (!Immutable.is(modelFiles.get(name), this._prevModelFiles.get(name))) {
                    updatedNames.push(name);
                }
            }
        );

        let reSort = false;
        let updateIndices = false;
        // Do the updates first before indices change (re-sort may be required)
        updatedNames.forEach(
            name => {
                const previousModelFile = this._prevModelFiles.get(name);
                const previousViewFileKey = ViewFileService.getModelFileKey(previousModelFile);
                let index = this._indices.get(previousViewFileKey);
                let oldViewFile = index != null ? newViewFiles.get(index) : null;

                if (oldViewFile == null) {
                    index = newViewFiles.findIndex(value => ViewFileService.getViewFileKey(value) === previousViewFileKey);
                    if (index >= 0) {
                        oldViewFile = newViewFiles.get(index);
                        this._indices.set(previousViewFileKey, index);
                    }
                }

                const newViewFile = ViewFileService.createViewFile(
                    modelFiles.get(name),
                    oldViewFile != null ? oldViewFile.isSelected : false
                );
                if (index != null && index >= 0) {
                    newViewFiles = newViewFiles.set(index, newViewFile);
                } else {
                    newViewFiles = newViewFiles.push(newViewFile);
                    this._indices.set(previousViewFileKey, newViewFiles.size - 1);
                }
                if (oldViewFile != null && this._sortComparator != null && this._sortComparator(oldViewFile, newViewFile) !== 0) {
                    reSort = true;
                }
            }
        );
        // Do the adds (requires re-sort)
        addedNames.forEach(
            name => {
                reSort = true;
                const viewFile = ViewFileService.createViewFile(modelFiles.get(name));
                newViewFiles = newViewFiles.push(viewFile);
                this._indices.set(ViewFileService.getViewFileKey(viewFile), newViewFiles.size - 1);
            }
        );
        // Do the removes (no re-sort required)
        removedNames.forEach(
            name => {
                updateIndices = true;
                const index = newViewFiles.findIndex(value => ViewFileService.getViewFileKey(value) === name);
                newViewFiles = newViewFiles.remove(index);
                this._indices.delete(name);
            }
        );

        if (reSort && this._sortComparator != null) {
            this._logger.debug("Re-sorting view files");
            updateIndices = true;
            newViewFiles = newViewFiles.sort(this._sortComparator).toList();
        }
        if (updateIndices) {
            this._indices.clear();
            newViewFiles.forEach(
                (value, index) => this._indices.set(ViewFileService.getViewFileKey(value), index)
            );
        }

        this._files = newViewFiles;
        this.pushViewFiles();
        this._prevModelFiles = modelFiles;
        this._logger.debug("New view model: %O", this._files.toJS());
    }

    get files(): Observable<Immutable.List<ViewFile>> {
        return this._filesSubject.asObservable();
    }

    get filteredFiles(): Observable<Immutable.List<ViewFile>> {
        return this._filteredFilesSubject.asObservable();
    }

    get totalFilteredCount(): Observable<number> {
        return this._totalFilteredCountSubject.asObservable();
    }

    get pageSize(): Observable<number> {
        return this._pageSizeSubject.asObservable();
    }

    get currentPage(): Observable<number> {
        return this._currentPageSubject.asObservable();
    }

    public setPageSize(size: number): void {
        this._pageSize = size;
        this._currentPage = 0;
        this._pageSizeSubject.next(size);
        this._currentPageSubject.next(this._currentPage);
        this.pushViewFiles();
    }

    public setPage(page: number): void {
        this._currentPage = page;
        this._currentPageSubject.next(this._currentPage);
        this.pushViewFiles();
    }

    public nextPage(): void {
        const totalPages = Math.ceil(this._totalFilteredCountSubject.getValue() / this._pageSize);
        if (this._currentPage < totalPages - 1) {
            this._currentPage++;
            this._currentPageSubject.next(this._currentPage);
            this.pushViewFiles();
        }
    }

    public prevPage(): void {
        if (this._currentPage > 0) {
            this._currentPage--;
            this._currentPageSubject.next(this._currentPage);
            this.pushViewFiles();
        }
    }

    /**
     * Set a file to be in selected state
     * @param {ViewFile} file
     */
    public setSelected(file: ViewFile) {
        // Find the selected file, if any
        // Note: we can optimize this by storing an additional
        //       state that tracks the selected file
        //       but that would duplicate state and can introduce
        //       bugs, so we just search instead
        let viewFiles = this._files;
        const unSelectIndex = viewFiles.findIndex(value => value.isSelected);

        // Unset the previously selected file, if any
        if (unSelectIndex >= 0) {
            let unSelectViewFile = viewFiles.get(unSelectIndex);

            // Do nothing if file is already selected
            if (ViewFileService.getViewFileKey(unSelectViewFile) === ViewFileService.getViewFileKey(file)) {
                return;
            }

            unSelectViewFile = new ViewFile(unSelectViewFile.set("isSelected", false));
            viewFiles = viewFiles.set(unSelectIndex, unSelectViewFile);
        }

        // Set the new selected file
        const fileKey = ViewFileService.getViewFileKey(file);
        if (this._indices.has(fileKey)) {
            const index = this._indices.get(fileKey);
            let viewFile = viewFiles.get(index);
            viewFile = new ViewFile(viewFile.set("isSelected", true));
            viewFiles = viewFiles.set(index, viewFile);
        } else {
            this._logger.error("Can't find file to select: " + file.name);
        }

        // Send update
        this._files = viewFiles;
        this.pushViewFiles();
    }

    /**
     * Un-select the currently selected file
     */
    public unsetSelected() {
        // Unset the previously selected file, if any
        let viewFiles = this._files;
        const unSelectIndex = viewFiles.findIndex(value => value.isSelected);

        // Unset the previously selected file, if any
        if (unSelectIndex >= 0) {
            let unSelectViewFile = viewFiles.get(unSelectIndex);

            unSelectViewFile = new ViewFile(unSelectViewFile.set("isSelected", false));
            viewFiles = viewFiles.set(unSelectIndex, unSelectViewFile);

            // Send update
            this._files = viewFiles;
            this.pushViewFiles();
        }
    }

    /**
     * Queue a file for download
     * @param {ViewFile} file
     * @returns {Observable<WebReaction>}
     */
    public queue(file: ViewFile): Observable<WebReaction> {
        this._logger.debug("Queue view file: " + file.name);
        return this.createAction(file, (f) => this.modelFileService.queue(f));
    }

    /**
     * Stop a file
     * @param {ViewFile} file
     * @returns {Observable<WebReaction>}
     */
    public stop(file: ViewFile): Observable<WebReaction> {
        this._logger.debug("Stop view file: " + file.name);
        return this.createAction(file, (f) => this.modelFileService.stop(f));
    }

    /**
     * Extract a file
     * @param {ViewFile} file
     * @returns {Observable<WebReaction>}
     */
    public extract(file: ViewFile): Observable<WebReaction> {
        this._logger.debug("Extract view file: " + file.name);
        return this.createAction(file, (f) => this.modelFileService.extract(f));
    }

    /**
     * Locally delete a file
     * @param {ViewFile} file
     * @returns {Observable<WebReaction>}
     */
    public deleteLocal(file: ViewFile): Observable<WebReaction> {
        this._logger.debug("Locally delete view file: " + file.name);
        return this.createAction(file, (f) => this.modelFileService.deleteLocal(f));
    }

    /**
     * Remotely delete a file
     * @param {ViewFile} file
     * @returns {Observable<WebReaction>}
     */
    public deleteRemote(file: ViewFile): Observable<WebReaction> {
        this._logger.debug("Remotely delete view file: " + file.name);
        return this.createAction(file, (f) => this.modelFileService.deleteRemote(f));
    }

    /**
     * Validate a file
     * @param {ViewFile} file
     * @returns {Observable<WebReaction>}
     */
    public validate(file: ViewFile): Observable<WebReaction> {
        this._logger.debug("Validate view file: " + file.name);
        return this.createAction(file, (f) => this.modelFileService.validate(f));
    }

    /**
     * Set a new filter criteria
     * @param {ViewFileFilterCriteria} criteria
     */
    public setFilterCriteria(criteria: ViewFileFilterCriteria) {
        this._filterCriteria = criteria;
        this._fileSelectionService.clear();
        this.pushViewFiles();
    }

    /**
     * Sets a new comparator.
     * @param {ViewFileComparator} comparator
     */
    public setComparator(comparator: ViewFileComparator) {
        this._sortComparator = comparator;
        this._fileSelectionService.clear();

        // Re-sort and regenerate index cache
        this._logger.debug("Re-sorting view files");
        let newViewFiles = this._files;
        if (this._sortComparator != null) {
            newViewFiles = newViewFiles.sort(this._sortComparator).toList();
        }
        this._files = newViewFiles;
        this._indices.clear();
        newViewFiles.forEach(
            (value, index) => this._indices.set(ViewFileService.getViewFileKey(value), index)
        );

        this.pushViewFiles();
    }

    private static createViewFile(modelFile: ModelFile, isSelected: boolean = false): ViewFile {
        // Use zero for unknown sizes
        let localSize: number = modelFile.local_size;
        if (localSize == null) {
            localSize = 0;
        }
        let remoteSize: number = modelFile.remote_size;
        if (remoteSize == null) {
            remoteSize = 0;
        }
        let transferredSize: number = modelFile.transferred_size;
        if (transferredSize == null) {
            transferredSize = localSize;
        }
        const isLocalOnly: boolean = [ModelFile.State.DEFAULT, ModelFile.State.DOWNLOADED].includes(modelFile.state)
            && localSize > 0
            && modelFile.remote_size == null;
        if (isLocalOnly) {
            transferredSize = localSize;
        }
        const displaySizeTotal: number = isLocalOnly ? localSize : remoteSize;
        const hasRetainedProgress: boolean = !isLocalOnly
            && (modelFile.remote_size == null || remoteSize > 0)
            && (
            transferredSize > 0 || (modelFile.download_progress != null && modelFile.download_progress > 0)
        );
        let percentDownloaded: number = 0;
        // Prefer the live transfer percentage for active downloads; fall back to size ratios otherwise.
        if (modelFile.state === ModelFile.State.DOWNLOADING && modelFile.download_progress != null) {
            percentDownloaded = modelFile.download_progress;
        } else if (modelFile.state === ModelFile.State.DEFAULT && hasRetainedProgress && modelFile.download_progress != null) {
            percentDownloaded = modelFile.download_progress;
        } else if (isLocalOnly) {
            percentDownloaded = 100;
        } else if (remoteSize > 0) {
            percentDownloaded = Math.round(100.0 * transferredSize / remoteSize);
        }

        // Translate the status
        let status = null;
        switch (modelFile.state) {
            case ModelFile.State.DEFAULT: {
                if (hasRetainedProgress) {
                    status = ViewFile.Status.STOPPED;
                } else {
                    status = ViewFile.Status.DEFAULT;
                }
                break;
            }
            case ModelFile.State.QUEUED: {
                status = ViewFile.Status.QUEUED;
                break;
            }
            case ModelFile.State.DOWNLOADING: {
                status = ViewFile.Status.DOWNLOADING;
                break;
            }
            case ModelFile.State.DOWNLOADED: {
                status = ViewFile.Status.DOWNLOADED;
                break;
            }
            case ModelFile.State.DELETED: {
                status = ViewFile.Status.DELETED;
                break;
            }
            case ModelFile.State.EXTRACTING: {
                status = ViewFile.Status.EXTRACTING;
                break;
            }
            case ModelFile.State.EXTRACTED: {
                status = ViewFile.Status.EXTRACTED;
                break;
            }
            case ModelFile.State.VALIDATING: {
                status = ViewFile.Status.VALIDATING;
                break;
            }
            case ModelFile.State.VALIDATED: {
                status = ViewFile.Status.VALIDATED;
                break;
            }
            case ModelFile.State.CORRUPT: {
                status = ViewFile.Status.CORRUPT;
                break;
            }
        }

        const isQueueable: boolean = [ViewFile.Status.DEFAULT,
                                    ViewFile.Status.STOPPED,
                                    ViewFile.Status.DELETED,
                                    ViewFile.Status.CORRUPT].includes(status)
                                    && remoteSize > 0;
        const isStoppable: boolean = [ViewFile.Status.QUEUED,
                                    ViewFile.Status.DOWNLOADING].includes(status)
                                    && modelFile.is_stoppable;
        const isExtractable: boolean = [ViewFile.Status.DEFAULT,
                                    ViewFile.Status.STOPPED,
                                    ViewFile.Status.DOWNLOADED,
                                    ViewFile.Status.EXTRACTED,
                                    ViewFile.Status.VALIDATED].includes(status)
                                    && localSize > 0;
        const isLocallyDeletable: boolean = [ViewFile.Status.DEFAULT,
                                    ViewFile.Status.STOPPED,
                                    ViewFile.Status.DOWNLOADED,
                                    ViewFile.Status.EXTRACTED,
                                    ViewFile.Status.VALIDATED,
                                    ViewFile.Status.CORRUPT].includes(status)
                                    && localSize > 0;
        const isRemotelyDeletable: boolean = [ViewFile.Status.DEFAULT,
                                    ViewFile.Status.STOPPED,
                                    ViewFile.Status.DOWNLOADED,
                                    ViewFile.Status.EXTRACTED,
                                    ViewFile.Status.DELETED,
                                    ViewFile.Status.VALIDATED,
                                    ViewFile.Status.CORRUPT].includes(status)
                                    && remoteSize > 0;
        const isValidatable: boolean = [ViewFile.Status.DOWNLOADED,
                                    ViewFile.Status.EXTRACTED,
                                    ViewFile.Status.VALIDATED,
                                    ViewFile.Status.CORRUPT].includes(status)
                                    && localSize > 0 && remoteSize > 0;

        return new ViewFile({
            fileId: modelFile.file_id,
            pathPairId: modelFile.path_pair_id,
            pathPairName: modelFile.path_pair_name,
            name: modelFile.name,
            isDir: modelFile.is_dir,
            localSize: localSize,
            remoteSize: remoteSize,
            transferredSize: transferredSize,
            displaySizeTotal: displaySizeTotal,
            percentDownloaded: percentDownloaded,
            status: status,
            downloadingSpeed: modelFile.downloading_speed,
            eta: modelFile.eta,
            fullPath: modelFile.full_path,
            isArchive: modelFile.is_extractable,
            isSelected: isSelected,
            isQueueable: isQueueable,
            isStoppable: isStoppable,
            isExtractable: isExtractable,
            isLocallyDeletable: isLocallyDeletable,
            isRemotelyDeletable: isRemotelyDeletable,
            isValidatable: isValidatable,
            localCreatedTimestamp: modelFile.local_created_timestamp,
            localModifiedTimestamp: modelFile.local_modified_timestamp,
            remoteCreatedTimestamp: modelFile.remote_created_timestamp,
            remoteModifiedTimestamp: modelFile.remote_modified_timestamp,
            validationProgress: modelFile.validation_progress,
            validationError: modelFile.validation_error,
            corruptChunks: modelFile.corrupt_chunks
        });
    }

    /**
     * Helper method to execute an action on ModelFileService and generate a ViewFileReaction
     * @param {ViewFile} file
     * @param {Observable<WebReaction>} action
     * @returns {Observable<WebReaction>}
     */
    private createAction(file: ViewFile,
                         action: (file: ModelFile) => Observable<WebReaction>)
            : Observable<WebReaction> {
        return Observable.create(observer => {
            const fileKey = file.fileId || file.name;
            if (!this._prevModelFiles.has(fileKey)) {
                // File not found, exit early
                this._logger.error("File to queue not found: " + file.name);
                observer.next(new WebReaction(false, null, `File '${file.name}' not found`));
            } else {
                const modelFile = this._prevModelFiles.get(fileKey);
                action(modelFile).subscribe(reaction => {
                    this._logger.debug("Received model reaction: %O", reaction);
                    observer.next(reaction);
                });
            }
        });
    }

    private pushViewFiles() {
        // Unfiltered files
        this._filesSubject.next(this._files);

        // Filtered files
        let filteredFiles = this._files;
        if (this._filterCriteria != null) {
            filteredFiles = Immutable.List<ViewFile>(
                this._files.filter(f => this._filterCriteria.meetsCriteria(f))
            );
        }

        const totalCount = filteredFiles.size;
        this._totalFilteredCountSubject.next(totalCount);

        const totalPages = this._pageSize > 0 ? Math.ceil(totalCount / this._pageSize) : 1;
        if (this._currentPage >= totalPages) {
            this._currentPage = Math.max(0, totalPages - 1);
            this._currentPageSubject.next(this._currentPage);
        }

        const start = this._currentPage * this._pageSize;
        const end = start + this._pageSize;
        const pagedFiles = filteredFiles.slice(start, end).toList();
        this._filteredFilesSubject.next(pagedFiles);
    }
}
