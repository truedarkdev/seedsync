import {Component, ChangeDetectionStrategy, OnDestroy} from "@angular/core";
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

export class FileListComponent implements OnDestroy {
    public files: Observable<List<ViewFile>>;
    public selectedNames: Observable<Immutable.Set<string>>;
    public selectedFiles: Observable<List<ViewFile>>;
    public areAllVisibleSelected: Observable<boolean>;
    public identify = FileListComponent.identify;
    public options: Observable<ViewFileOptions>;
    public SortMethod = ViewFileOptions.SortMethod;
    private _filesSubscription: Subscription;

    constructor(private _logger: LoggerService,
                private viewFileService: ViewFileService,
                private viewFileOptionsService: ViewFileOptionsService,
                private fileSelectionService: FileSelectionService) {
        this.files = viewFileService.filteredFiles;
        this.selectedNames = fileSelectionService.selectedNames;
        this.selectedFiles = fileSelectionService.selectedFiles;
        this.areAllVisibleSelected = fileSelectionService.areAllVisibleSelected;
        this.options = this.viewFileOptionsService.options;
        this._filesSubscription = this.files.subscribe(files => this.fileSelectionService.setVisibleFiles(files));
    }

    static identify(index: number, item: ViewFile): string {
        return item.name;
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
}
