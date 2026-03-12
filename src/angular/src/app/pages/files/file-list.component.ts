import {ChangeDetectorRef, ChangeDetectionStrategy, OnDestroy, OnInit} from "@angular/core";
import {Observable} from "rxjs/Observable";
import {Subscription} from "rxjs/Subscription";

import {List} from "immutable";
import * as Immutable from "immutable";

import {ViewFileService} from "../../services/files/view-file.service";
import {ViewFile} from "../../services/files/view-file";
import {LoggerService} from "../../services/utils/logger.service";
import {ViewFileOptions} from "../../services/files/view-file-options";
import {ViewFileOptionsService} from "../../services/files/view-file-options.service";
import {FileSelectionService} from "../../services/files/file-selection.service";

@Component({
    selector: "app-file-list",
    providers: [],
    templateUrl: "./file-list.component.html",
    styleUrls: ["./file-list.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class FileListComponent implements OnInit, OnDestroy {
    public files: Observable<List<ViewFile>>;
    public selectedFileIds: Observable<Immutable.Set<string>>;
    public selectedFiles: Observable<List<ViewFile>>;
    public areAllVisibleSelected: Observable<boolean>;
    public identify = FileListComponent.identify;
    public options: Observable<ViewFileOptions>;
    public SortMethod = ViewFileOptions.SortMethod;
    private _filesSubscription: Subscription;
    private _paginationSubscription: Subscription;
    public totalCount = 0;
    public currentPage = 0;
    public pageSize = 50;
    public totalPages = 0;
    public readonly PAGE_SIZES = [25, 50, 100];

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
        this._filesSubscription = this.files.subscribe(files => this.fileSelectionService.setVisibleFiles(files));
    }

    ngOnInit(): void {
        const savedPageSize = localStorage.getItem("dashboard_page_size");
        if (savedPageSize && this.PAGE_SIZES.indexOf(+savedPageSize) >= 0) {
            this.pageSize = +savedPageSize;
            this.viewFileService.setPageSize(this.pageSize);
        }

        this._paginationSubscription = Observable.combineLatest(
            this.viewFileService.totalFilteredCount,
            this.viewFileService.currentPage,
            this.viewFileService.pageSize,
            (totalCount: number, currentPage: number, pageSize: number) => {
                this.totalCount = totalCount;
                this.currentPage = currentPage;
                this.pageSize = pageSize;
                this.totalPages = pageSize > 0 ? Math.ceil(totalCount / pageSize) : 1;
                this._changeDetector.markForCheck();
            }
        ).subscribe();
    }

    static identify(index: number, item: ViewFile): string {
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

    isSortedBy(currentSortMethod: ViewFileOptions.SortMethod,
               primarySortMethod: ViewFileOptions.SortMethod,
               secondarySortMethod: ViewFileOptions.SortMethod): boolean {
        return currentSortMethod === primarySortMethod || currentSortMethod === secondarySortMethod;
    }

    isSortDescending(currentSortMethod: ViewFileOptions.SortMethod,
                     descendingSortMethod: ViewFileOptions.SortMethod): boolean {
        return currentSortMethod === descendingSortMethod;
    }

    onQueue(file: ViewFile) {
        this.viewFileService.queue(file).subscribe(data => {
            this._logger.info(data);
        });
    }

    onStop(file: ViewFile) {
        this.viewFileService.stop(file).subscribe(data => {
            this._logger.info(data);
        });
    }

    onExtract(file: ViewFile) {
        this.viewFileService.extract(file).subscribe(data => {
            this._logger.info(data);
        });
    }

    onDeleteLocal(file: ViewFile) {
        this.viewFileService.deleteLocal(file).subscribe(data => {
            this._logger.info(data);
        });
    }

    onDeleteRemote(file: ViewFile) {
        this.viewFileService.deleteRemote(file).subscribe(data => {
            this._logger.info(data);
        });
    }

    onValidate(file: ViewFile) {
        this.viewFileService.validate(file).subscribe(data => {
            this._logger.info(data);
        });
    }

    onPageSizeChange(newSize: number): void {
        this.pageSize = newSize;
        localStorage.setItem("dashboard_page_size", String(newSize));
        this.viewFileService.setPageSize(newSize);
    }

    onPrevPage(): void {
        this.viewFileService.prevPage();
    }

    onNextPage(): void {
        this.viewFileService.nextPage();
    }

    get pageStart(): number {
        return this.totalCount === 0 ? 0 : this.currentPage * this.pageSize + 1;
    }

    get pageEnd(): number {
        return Math.min((this.currentPage + 1) * this.pageSize, this.totalCount);
    }
}
