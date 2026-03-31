import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import {Observable} from "rxjs/Observable";
import {Subscription} from "rxjs/Subscription";

import * as Immutable from "immutable";

import {ViewFileOptionsService} from "../../services/files/view-file-options.service";
import {ViewFileOptions} from "../../services/files/view-file-options";
import {ViewFile} from "../../services/files/view-file";
import {ViewFileService} from "../../services/files/view-file.service";
import {DomService} from "../../services/utils/dom.service";

@Component({
    selector: "app-file-options",
    standalone: false,
    providers: [],
    templateUrl: "./file-options.component.html",
    styleUrls: ["./file-options.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class FileOptionsComponent implements OnInit, OnDestroy {
    public ViewFile = ViewFile;
    public ViewFileOptions = ViewFileOptions;

    public statusCounts = {
        [ViewFile.Status.EXTRACTED]: 0,
        [ViewFile.Status.EXTRACTING]: 0,
        [ViewFile.Status.DOWNLOADED]: 0,
        [ViewFile.Status.DOWNLOADING]: 0,
        [ViewFile.Status.QUEUED]: 0,
        [ViewFile.Status.STOPPED]: 0
    };

    public options: Observable<ViewFileOptions>;
    public headerHeight: Observable<number>;

    private _latestOptions: ViewFileOptions;
    private _filesSubscription: Subscription;
    private _optionsSubscription: Subscription;
    private readonly _windowScrollListener = () => this.closeOpenDropdowns();

    constructor(private _changeDetector: ChangeDetectorRef,
                private viewFileOptionsService: ViewFileOptionsService,
                private _viewFileService: ViewFileService,
                private _domService: DomService) {
        this.options = this.viewFileOptionsService.options;
        this.headerHeight = this._domService.headerHeight;
    }

    ngOnInit() {
        // Use the unfiltered files so filter counts reflect the full file list.
        this._filesSubscription = this._viewFileService.files.subscribe(files => {
            this.statusCounts[ViewFile.Status.EXTRACTED] = FileOptionsComponent.getStatusCount(
                files, ViewFile.Status.EXTRACTED
            );
            this.statusCounts[ViewFile.Status.EXTRACTING] = FileOptionsComponent.getStatusCount(
                files, ViewFile.Status.EXTRACTING
            );
            this.statusCounts[ViewFile.Status.DOWNLOADED] = FileOptionsComponent.getStatusCount(
                files, ViewFile.Status.DOWNLOADED
            );
            this.statusCounts[ViewFile.Status.DOWNLOADING] = FileOptionsComponent.getStatusCount(
                files, ViewFile.Status.DOWNLOADING
            );
            this.statusCounts[ViewFile.Status.QUEUED] = FileOptionsComponent.getStatusCount(
                files, ViewFile.Status.QUEUED
            );
            this.statusCounts[ViewFile.Status.STOPPED] = FileOptionsComponent.getStatusCount(
                files, ViewFile.Status.STOPPED
            );
            this._changeDetector.detectChanges();
        });

        // Keep the latest options for toggle behaviour implementation
        this._optionsSubscription = this.viewFileOptionsService.options.subscribe(options => this._latestOptions = options);

        window.addEventListener("scroll", this._windowScrollListener, true);
    }

    ngOnDestroy() {
        if (this._filesSubscription) {
            this._filesSubscription.unsubscribe();
        }
        if (this._optionsSubscription) {
            this._optionsSubscription.unsubscribe();
        }
        window.removeEventListener("scroll", this._windowScrollListener, true);
    }

    onFilterByName(name: string) {
        this.viewFileOptionsService.setNameFilter(name);
    }

    onFilterByStatus(status: ViewFile.Status) {
        this.viewFileOptionsService.setSelectedStatusFilter(status);
    }

    onSort(sortMethod: ViewFileOptions.SortMethod) {
        this.viewFileOptionsService.setSortMethod(sortMethod);
    }

    onToggleShowDetails(){
        this.viewFileOptionsService.setShowDetails(!this._latestOptions.showDetails);
    }

    onTogglePinFilter() {
        this.viewFileOptionsService.setPinFilter(!this._latestOptions.pinFilter);
    }

    public getStatusCount(status: ViewFile.Status) {
        return this.statusCounts[status] || 0;
    }

    public isStatusAvailable(status: ViewFile.Status) {
        return this.getStatusCount(status) > 0;
    }

    public isStatusDisabled(status: ViewFile.Status) {
        return !this.isStatusAvailable(status) &&
            (!this._latestOptions || this._latestOptions.selectedStatusFilter !== status);
    }

    private closeOpenDropdowns() {
        const fileOptions = document.getElementById("file-options");
        if (fileOptions == null) {
            return;
        }

        Array.from(fileOptions.querySelectorAll(".dropdown"))
            .forEach(dropdown => dropdown.classList.remove("show", "open"));
        Array.from(fileOptions.querySelectorAll(".dropdown-menu"))
            .forEach(menu => menu.classList.remove("show"));
        Array.from(fileOptions.querySelectorAll(".dropdown-toggle"))
            .forEach(button => {
                button.classList.remove("show");
                button.setAttribute("aria-expanded", "false");
            });
    }

    private static getStatusCount(files: Immutable.List<ViewFile>, status: ViewFile.Status) {
        return files.count(file => file.status === status);
    }
}
