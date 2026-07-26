import {
    AfterViewChecked, AfterViewInit, ChangeDetectorRef, ChangeDetectionStrategy, Component,
    ElementRef, OnDestroy, OnInit, QueryList, ViewChild, ViewChildren
} from "@angular/core";
import {CommonModule} from "@angular/common";
import {FormsModule} from "@angular/forms";
import {combineLatest, Observable, Subscription} from "rxjs";

import {List} from "immutable";
import * as Immutable from "immutable";

import {ViewFileService} from "../../services/files/view-file.service";
import {ViewFile} from "../../services/files/view-file";
import {LoggerService} from "../../services/utils/logger.service";
import {ViewFileOptions} from "../../services/files/view-file-options";
import {ViewFileOptionsService} from "../../services/files/view-file-options.service";
import {FileSelectionService} from "../../services/files/file-selection.service";
import {WebReaction} from "../../services/utils/rest.service";
import {FileAction, FileComponent} from "./file.component";
import {BulkActionsBarComponent} from "./bulk-actions-bar.component";

type FileActionReleasePolicy = "response" | "model";

@Component({
    selector: "app-file-list",
    standalone: true,
    imports: [CommonModule, FormsModule, BulkActionsBarComponent, FileComponent],
    providers: [],
    templateUrl: "./file-list.component.html",
    styleUrls: ["./file-list.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class FileListComponent implements OnInit, AfterViewInit, AfterViewChecked, OnDestroy {
    public files: Observable<List<ViewFile>>;
    /** The complete logical page. Rendering is windowed independently below. */
    public logicalFiles: List<ViewFile> = List<ViewFile>();
    public visibleFiles: ViewFile[] = [];
    public renderedStartIndex = 0;
    public renderedEndIndex = 0;
    public topSpacerHeight = 0;
    public bottomSpacerHeight = 0;
    public selectedFileIds: Observable<Immutable.Set<string>>;
    public selectedFiles: Observable<List<ViewFile>>;
    public areAllVisibleSelected: Observable<boolean>;
    public identify = FileListComponent.identify;
    public options: Observable<ViewFileOptions>;
    public SortMethod = ViewFileOptions.SortMethod;
    @ViewChildren(FileComponent) private fileComponents: QueryList<FileComponent>;
    private _filesSubscription: Subscription;
    private _paginationSubscription: Subscription;
    public totalCount = 0;
    public currentPage = 0;
    public pageSize = 50;
    public totalPages = 0;
    public readonly PAGE_SIZES = [25, 50, 100, 1000, 0];

    // Natural-height rows cannot use a fixed CDK itemSize. Keep a deterministic
    // estimate until each rendered row has reported its actual height.
    private static readonly DEFAULT_ROW_HEIGHT = 80;
    private static readonly OVERSCAN_PX = 600;
    private static readonly WINDOWING_THRESHOLD = 100;
    // A hard row cap keeps pathological/very short rows from expanding the
    // mounted slice to the entire 1000/All page.
    private static readonly MAX_RENDERED_ROWS = 80;
    private static readonly ACTION_RELEASE_POLICIES: {[action: number]: FileActionReleasePolicy} = {
        [FileAction.QUEUE]: "model",
        [FileAction.STOP]: "response",
        [FileAction.EXTRACT]: "model",
        [FileAction.DELETE_LOCAL]: "response",
        [FileAction.DELETE_REMOTE]: "model",
        [FileAction.VALIDATE]: "model",
        [FileAction.RETRY_MOVE]: "response"
    };
    private readonly _rowHeights = new Map<string, number>();
    private _heightKeys: string[] = [];
    private _heightPrefix: number[] = [0];
    private _heightIndexDirty = true;
    private _heightIndexRebuilds = 0;
    // Row components are windowed and may be destroyed while an action is
    // still in flight. Keep the action lock at the list boundary so a remount
    // cannot start a duplicate operation.
    private readonly _activeActions = new Map<string, FileAction>();
    private readonly _lastFileStates = new Map<string, ViewFile>();
    private _rowsSubscription: Subscription;
    private _resizeObserver: ResizeObserver | null = null;
    private _windowScrollListener: () => void;
    private _windowResizeListener: () => void;
    private _pendingFrame: number | null = null;
    private _scrollOffset = 0;
    private _viewportStartIndex = 0;
    private _measurementInProgress = false;
    private _lastKeys = "";

    @ViewChild("rowsContainer") private rowsContainer: ElementRef<HTMLElement>;
    @ViewChildren("fileRow", {read: ElementRef}) private fileRows: QueryList<ElementRef<HTMLElement>>;

    constructor(private _logger: LoggerService,
                private viewFileService: ViewFileService,
                private viewFileOptionsService: ViewFileOptionsService,
                private fileSelectionService: FileSelectionService,
                private _changeDetector: ChangeDetectorRef) {
        this.files = viewFileService.filteredFiles;
        this.selectedFileIds = fileSelectionService.selectedFileIds;
        this.selectedFiles = fileSelectionService.selectedFiles;
        this.areAllVisibleSelected = fileSelectionService.areAllVisibleSelected;
        this.options = this.viewFileOptionsService.options;
        this._filesSubscription = this.files.subscribe(files => {
            this.reconcileFileActions(files);
            this.logicalFiles = files;
            this.fileSelectionService.setVisibleFiles(files);
            this.pruneRowHeights(files);
            this.scheduleWindowUpdate();
        });
    }

    ngOnInit(): void {
        this._paginationSubscription = combineLatest([
            this.viewFileService.totalFilteredCount,
            this.viewFileService.currentPage,
            this.viewFileService.pageSize
        ]).subscribe(([totalCount, currentPage, pageSize]: [number, number, number]) => {
            this.totalCount = totalCount;
            this.currentPage = currentPage;
            this.pageSize = pageSize;
            this.totalPages = pageSize > 0 ? Math.ceil(totalCount / pageSize) : 1;
            this._changeDetector.markForCheck();
        });
    }

    ngAfterViewInit(): void {
        this._windowScrollListener = () => this.onWindowScroll();
        this._windowResizeListener = () => this.onWindowResize();
        const currentWindow = this.getWindow();
        if (currentWindow != null) {
            currentWindow.addEventListener("scroll", this._windowScrollListener, {passive: true});
            currentWindow.addEventListener("resize", this._windowResizeListener);
        }

        if (this.fileRows != null) {
            this._rowsSubscription = this.fileRows.changes.subscribe(() => this.onRenderedRowsChanged());
            this.onRenderedRowsChanged();
        }
        this.onWindowScroll();
        this.scheduleWindowUpdate();
    }

    ngAfterViewChecked(): void {
        // Older browsers without ResizeObserver still get deterministic height
        // updates after Angular has rendered the current logical slice.
        if (this._resizeObserver == null) {
            this.measureRenderedRows();
        }
    }

    static identify(_index: number, item: ViewFile): string {
        return item.fileId || item.name;
    }

    onSelect(file: ViewFile): void {
        if (file.isSelected) {
            this.viewFileService.unsetSelected();
        } else {
            this.viewFileService.setSelected(file);
        }
    }

    ngOnDestroy(): void {
        this._filesSubscription.unsubscribe();
        if (this._paginationSubscription) {
            this._paginationSubscription.unsubscribe();
        }
        if (this._rowsSubscription) {
            this._rowsSubscription.unsubscribe();
        }
        this._resizeObserver?.disconnect();
        const currentWindow = this.getWindow();
        if (currentWindow != null) {
            if (this._windowScrollListener) {
                currentWindow.removeEventListener("scroll", this._windowScrollListener);
            }
            if (this._windowResizeListener) {
                currentWindow.removeEventListener("resize", this._windowResizeListener);
            }
            if (this._pendingFrame != null && typeof currentWindow.cancelAnimationFrame === "function") {
                currentWindow.cancelAnimationFrame(this._pendingFrame);
            }
        }
        this._pendingFrame = null;
    }

    /** Scroll the page, not a nested fixed-height list, while updating the window. */
    onWindowScroll(scrollOffset?: number): void {
        if (scrollOffset != null && Number.isFinite(scrollOffset)) {
            this._scrollOffset = Math.max(0, scrollOffset);
        } else {
            const currentWindow = this.getWindow();
            this._scrollOffset = currentWindow == null ? 0 :
                Math.max(0, currentWindow.scrollY || currentWindow.pageYOffset || 0);
        }
        this.scheduleWindowUpdate();
    }

    onWindowResize(): void {
        // A width change can alter wrapping for rows that are currently
        // unmounted, so stale measurements must not shape the next window.
        this._rowHeights.clear();
        this._heightIndexDirty = true;
        this.scheduleWindowUpdate();
    }

    onSelectionToggle(file: ViewFile): void {
        this.fileSelectionService.toggle(file);
    }

    onToggleAllVisible(checked: boolean): void {
        this.fileSelectionService.setAllVisibleSelected(checked);
    }

    onSort(currentSortMethod: ViewFileOptions.SortMethod,
           primarySortMethod: ViewFileOptions.SortMethod,
           secondarySortMethod: ViewFileOptions.SortMethod): void {
        if (currentSortMethod === primarySortMethod) {
            this.viewFileOptionsService.setSortMethod(secondarySortMethod);
        } else {
            this.viewFileOptionsService.setSortMethod(primarySortMethod);
        }
    }

    onStatusSort(currentSortMethod: ViewFileOptions.SortMethod): void {
        this.viewFileOptionsService.setSortMethod(
            currentSortMethod === ViewFileOptions.SortMethod.STATUS_DESC ?
                ViewFileOptions.SortMethod.SMART_STATUS :
                ViewFileOptions.SortMethod.STATUS_DESC
        );
    }

    isSortedBy(currentSortMethod: ViewFileOptions.SortMethod,
               primarySortMethod: ViewFileOptions.SortMethod,
               secondarySortMethod: ViewFileOptions.SortMethod): boolean {
        return currentSortMethod === primarySortMethod || currentSortMethod === secondarySortMethod;
    }

    isStatusSorted(currentSortMethod: ViewFileOptions.SortMethod): boolean {
        return currentSortMethod === ViewFileOptions.SortMethod.SMART_STATUS ||
            currentSortMethod === ViewFileOptions.SortMethod.STATUS ||
            currentSortMethod === ViewFileOptions.SortMethod.STATUS_DESC;
    }

    isSortDescending(currentSortMethod: ViewFileOptions.SortMethod,
                     descendingSortMethod: ViewFileOptions.SortMethod): boolean {
        return currentSortMethod === descendingSortMethod;
    }

    onQueue(file: ViewFile) {
        this.runFileAction(file, FileAction.QUEUE, () => this.viewFileService.queue(file));
    }

    onStop(file: ViewFile) {
        this.runFileAction(file, FileAction.STOP, () => this.viewFileService.stop(file));
    }

    onExtract(file: ViewFile) {
        this.runFileAction(file, FileAction.EXTRACT, () => this.viewFileService.extract(file));
    }

    onDeleteLocal(file: ViewFile) {
        this.runFileAction(file, FileAction.DELETE_LOCAL, () => this.viewFileService.deleteLocal(file));
    }

    onDeleteRemote(file: ViewFile) {
        this.runFileAction(file, FileAction.DELETE_REMOTE, () => this.viewFileService.deleteRemote(file));
    }

    onValidate(file: ViewFile) {
        this.runFileAction(file, FileAction.VALIDATE, () => this.viewFileService.validate(file));
    }

    onRetryMove(file: ViewFile) {
        this.runFileAction(file, FileAction.RETRY_MOVE, () => this.viewFileService.retryMove(file));
    }

    activeActionFor(file: ViewFile): FileAction {
        return file == null ? null : this._activeActions.get(FileListComponent.identify(0, file)) ?? null;
    }

    onPageSizeChange(newSize: number | string): void {
        const pageSize = +newSize;
        if (Number.isNaN(pageSize)) {
            return;
        }

        this.pageSize = pageSize;
        this.viewFileService.setPageSize(pageSize);
        this.scheduleWindowUpdate();
    }

    onPrevPage(): void {
        this.viewFileService.prevPage();
    }

    onNextPage(): void {
        this.viewFileService.nextPage();
    }

    get pageStart(): number {
        if (this.pageSize <= 0) {
            return this.totalCount === 0 ? 0 : 1;
        }

        return this.totalCount === 0 ? 0 : this.currentPage * this.pageSize + 1;
    }

    get pageEnd(): number {
        if (this.pageSize <= 0) {
            return this.totalCount;
        }

        return Math.min((this.currentPage + 1) * this.pageSize, this.totalCount);
    }

    private runFileAction(file: ViewFile, action: FileAction, requestFactory: () => Observable<WebReaction>): void {
        if (!this.beginFileAction(file, action)) {
            return;
        }
        let request: Observable<WebReaction>;
        try {
            request = requestFactory();
        } catch {
            this.resetFileLoading(file, action);
            return;
        }
        let acknowledged = false;
        request.subscribe({
            next: data => {
                acknowledged = true;
                this._logger.info(data);
                if (this.isResponseDrivenAction(action) || data?.success === false) {
                    this.resetFileLoading(file, action);
                }
            },
            error: () => this.resetFileLoading(file, action),
            complete: () => {
                // HTTP action streams normally emit one reaction then complete.
                // A completion-only stream has no acknowledgement and must not
                // leave a remounted row locked indefinitely. Successful
                // status-driven acknowledgements stay locked until the model
                // stream reports the corresponding transition.
                if (this.isResponseDrivenAction(action) || !acknowledged) {
                    this.resetFileLoading(file, action);
                }
            }
        });
    }

    private isResponseDrivenAction(action: FileAction): boolean {
        return FileListComponent.ACTION_RELEASE_POLICIES[action] === "response";
    }

    private beginFileAction(file: ViewFile, action: FileAction): boolean {
        if (file == null) {
            return false;
        }
        const fileKey = FileListComponent.identify(0, file);
        if (this._activeActions.has(fileKey)) {
            return false;
        }
        if (!this._lastFileStates.has(fileKey)) {
            this._lastFileStates.set(fileKey, file);
        }
        this._activeActions.set(fileKey, action);
        this._changeDetector.markForCheck();
        return true;
    }

    private resetFileLoading(file: ViewFile, action: FileAction): void {
        if (file == null) {
            return;
        }

        const fileKey = FileListComponent.identify(0, file);
        if (action == null || this._activeActions.get(fileKey) !== action) {
            return;
        }
        this._activeActions.delete(fileKey);
        this._changeDetector.markForCheck();

        if (this.fileComponents == null) {
            return;
        }

        const fileComponent = this.fileComponents.toArray().find(component => {
            const componentFileKey = component.file == null ? null : FileListComponent.identify(0, component.file);
            return componentFileKey === fileKey;
        });

        if (fileComponent != null) {
            fileComponent.resetActiveAction(file, action);
        }
    }

    private getWindow(): Window | null {
        return typeof window === "undefined" ? null : window;
    }

    private pruneRowHeights(files: List<ViewFile>): void {
        const keys = new Set(files.toArray().map(file => this.identify(0, file)));
        for (const key of this._rowHeights.keys()) {
            if (!keys.has(key)) {
                this._rowHeights.delete(key);
            }
        }

        const nextKeys = files.toArray().map(file => this.identify(0, file)).join("\u0000");
        if (nextKeys !== this._lastKeys) {
            this._lastKeys = nextKeys;
            this._viewportStartIndex = 0;
            this._heightIndexDirty = true;
        }
    }

    private reconcileFileActions(files: List<ViewFile>): void {
        const currentKeys = new Set<string>();
        files.forEach(file => {
            const fileKey = FileListComponent.identify(0, file);
            currentKeys.add(fileKey);
            const previousFile = this._lastFileStates.get(fileKey);
            if (previousFile != null && this._activeActions.has(fileKey) &&
                this.hasModelTransition(previousFile, file)) {
                this.resetFileLoading(file, this._activeActions.get(fileKey));
            }
            this._lastFileStates.set(fileKey, file);
        });

        // Keep baselines for in-flight actions while allowing filtered/paged
        // lists to release state once a file is permanently gone.
        for (const [fileKey] of this._lastFileStates) {
            if (!currentKeys.has(fileKey) && !this._activeActions.has(fileKey)) {
                this._lastFileStates.delete(fileKey);
            }
        }
    }

    private hasModelTransition(previousFile: ViewFile, currentFile: ViewFile): boolean {
        const action = this._activeActions.get(FileListComponent.identify(0, currentFile));
        switch (action) {
            case FileAction.QUEUE:
                // Queue acceptance is reflected by the transfer entering its
                // queued/downloading lifecycle; unrelated terminal/status
                // changes must not unlock the pending queue request.
                return previousFile.status !== currentFile.status &&
                    [ViewFile.Status.QUEUED, ViewFile.Status.DOWNLOADING].includes(currentFile.status);
            case FileAction.EXTRACT:
                return previousFile.status !== currentFile.status &&
                    [ViewFile.Status.EXTRACTING, ViewFile.Status.EXTRACTED].includes(currentFile.status);
            case FileAction.VALIDATE:
                return previousFile.status !== currentFile.status &&
                    [ViewFile.Status.VALIDATING,
                    ViewFile.Status.VALIDATED,
                    ViewFile.Status.CORRUPT].includes(currentFile.status);
            case FileAction.DELETE_REMOTE:
                // Remote deletion is complete only when the remote-delete
                // capability disappears; a status-only change is unrelated.
                return previousFile.isRemotelyDeletable && !currentFile.isRemotelyDeletable;
            default:
                // Response-driven actions are cleared by their request
                // lifecycle, while retaining no broader status fallback here.
                return false;
        }
    }

    private onRenderedRowsChanged(): void {
        this.refreshResizeObserver();
        this.measureRenderedRows();
    }

    private refreshResizeObserver(): void {
        this._resizeObserver?.disconnect();
        this._resizeObserver = null;
        if (typeof ResizeObserver === "undefined" || this.fileRows == null) {
            return;
        }

        this._resizeObserver = new ResizeObserver(entries => {
            let changed = false;
            for (const entry of entries) {
                const element = entry.target as HTMLElement;
                const key = element.getAttribute("data-file-key");
                if (key == null) {
                    continue;
                }
                const height = entry.contentRect.height || element.getBoundingClientRect().height;
                changed = this.updateRowHeight(key, height) || changed;
            }
            if (changed) {
                this.scheduleWindowUpdate();
            }
        });
        this.fileRows.forEach(row => this._resizeObserver.observe(row.nativeElement));
    }

    private measureRenderedRows(): void {
        if (this._measurementInProgress || this.fileRows == null) {
            return;
        }

        this._measurementInProgress = true;
        let changed = false;
        try {
            this.fileRows.forEach(row => {
                const element = row.nativeElement;
                const key = element.getAttribute("data-file-key");
                if (key == null) {
                    return;
                }
                changed = this.updateRowHeight(key, element.getBoundingClientRect().height) || changed;
            });
        } finally {
            this._measurementInProgress = false;
        }

        if (changed) {
            this.scheduleWindowUpdate();
        }
    }

    private updateRowHeight(key: string, height: number): boolean {
        if (!Number.isFinite(height) || height <= 0) {
            return false;
        }
        const previous = this._rowHeights.get(key);
        if (previous != null && Math.abs(previous - height) < 0.5) {
            return false;
        }
        this._rowHeights.set(key, height);
        this._heightIndexDirty = true;
        return true;
    }

    private scheduleWindowUpdate(): void {
        // Populate the first slice synchronously before the view has a rows
        // container; later updates are coalesced to one animation frame.
        if (this.rowsContainer == null) {
            this.updateVisibleRange();
            return;
        }
        const currentWindow = this.getWindow();
        if (currentWindow == null || typeof currentWindow.requestAnimationFrame !== "function") {
            this.updateVisibleRange();
            return;
        }
        if (this._pendingFrame != null) {
            return;
        }
        this._pendingFrame = currentWindow.requestAnimationFrame(() => {
            this._pendingFrame = null;
            this.updateVisibleRange();
        });
    }

    private updateVisibleRange(): void {
        const files = this.logicalFiles;
        const count = files.size;
        if (count === 0) {
            this.visibleFiles = [];
            this.renderedStartIndex = 0;
            this.renderedEndIndex = 0;
            this.topSpacerHeight = 0;
            this.bottomSpacerHeight = 0;
            this._changeDetector.markForCheck();
            return;
        }

        this.ensureHeightIndex();
        const prefix = this._heightPrefix;

        const shouldWindow = count > FileListComponent.WINDOWING_THRESHOLD;
        const viewportHeight = this.getViewportHeight();
        const rowsOffset = this.getRowsDocumentOffset();
        const scrollTop = Math.max(0, this._scrollOffset - rowsOffset);
        const overscan = FileListComponent.OVERSCAN_PX;
        const totalHeight = prefix[count];

        let start = 0;
        let end = count;
        if (shouldWindow) {
            start = this.findIndexAtOffset(prefix, Math.max(0, scrollTop - overscan));
            end = Math.min(count, this.findIndexAtOffset(prefix, Math.min(totalHeight, scrollTop + viewportHeight + overscan)) + 1);
            if (end <= start) {
                end = Math.min(count, start + 1);
            }

            if (end - start > FileListComponent.MAX_RENDERED_ROWS) {
                const viewportIndex = this.findIndexAtOffset(prefix, Math.min(totalHeight, scrollTop));
                start = Math.max(0, viewportIndex - Math.floor(FileListComponent.MAX_RENDERED_ROWS / 2));
                end = Math.min(count, start + FileListComponent.MAX_RENDERED_ROWS);
                if (end - start < FileListComponent.MAX_RENDERED_ROWS) {
                    start = Math.max(0, end - FileListComponent.MAX_RENDERED_ROWS);
                }
            }
        }

        this._viewportStartIndex = this.findIndexAtOffset(prefix, Math.min(totalHeight, scrollTop));
        this.renderedStartIndex = start;
        this.renderedEndIndex = end;
        this.topSpacerHeight = prefix[start];
        this.bottomSpacerHeight = Math.max(0, totalHeight - prefix[end]);
        this.visibleFiles = files.slice(start, end).toArray();
        this._changeDetector.markForCheck();
    }

    private ensureHeightIndex(): void {
        if (!this._heightIndexDirty && this._heightKeys.length === this.logicalFiles.size) {
            return;
        }

        const files = this.logicalFiles;
        this._heightKeys = new Array<string>(files.size);
        this._heightPrefix = new Array<number>(files.size + 1);
        this._heightPrefix[0] = 0;
        for (let index = 0; index < files.size; index++) {
            const key = this.identify(index, files.get(index));
            this._heightKeys[index] = key;
            this._heightPrefix[index + 1] = this._heightPrefix[index] +
                (this._rowHeights.get(key) || FileListComponent.DEFAULT_ROW_HEIGHT);
        }
        this._heightIndexDirty = false;
        this._heightIndexRebuilds += 1;
    }

    private findIndexAtOffset(prefix: number[], offset: number): number {
        let low = 0;
        let high = prefix.length - 1;
        while (low < high) {
            const middle = Math.floor((low + high) / 2);
            if (prefix[middle] <= offset) {
                low = middle + 1;
            } else {
                high = middle;
            }
        }
        return Math.max(0, Math.min(prefix.length - 2, low - 1));
    }

    private getViewportHeight(): number {
        const currentWindow = this.getWindow();
        return Math.max(1, currentWindow?.innerHeight || 800);
    }

    private getRowsDocumentOffset(): number {
        const rowsElement = this.rowsContainer?.nativeElement;
        if (rowsElement == null) {
            return 0;
        }
        const currentWindow = this.getWindow();
        const scrollY = currentWindow == null ? this._scrollOffset :
            (currentWindow.scrollY || currentWindow.pageYOffset || this._scrollOffset);
        return rowsElement.getBoundingClientRect().top + scrollY;
    }
}
