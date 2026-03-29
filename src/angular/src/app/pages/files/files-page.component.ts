import {Component, OnDestroy, OnInit} from "@angular/core";
import {ActivatedRoute} from "@angular/router";
import {Subject} from "rxjs/Subject";
import "rxjs/add/operator/takeUntil";

import {PathPair, PathPairService} from "../../services/settings/path-pair.service";
import {ViewFileFilterService} from "../../services/files/view-file-filter.service";

@Component({
    selector: "app-files-page",
    templateUrl: "./files-page.component.html"
})

export class FilesPageComponent implements OnInit, OnDestroy {
    public showOverview = false;
    public showDetailView = true;

    private readonly _destroy$ = new Subject<void>();
    private _pathPairs: PathPair[] = [];
    private _pathPairId: string = null;

    constructor(private _route: ActivatedRoute,
                private _pathPairService: PathPairService,
                private _viewFileFilterService: ViewFileFilterService) {
    }

    ngOnInit(): void {
        this._route.params.takeUntil(this._destroy$).subscribe({
            next: params => {
                this._pathPairId = params["pathPairId"] || null;
                this._updateRouteMode();
            }
        });

        this._pathPairService.pathPairs.takeUntil(this._destroy$).subscribe({
            next: (pathPairs: PathPair[]) => {
                this._pathPairs = pathPairs || [];
                this._updateRouteMode();
            }
        });

        this._updateRouteMode();
    }

    ngOnDestroy(): void {
        this._viewFileFilterService.setPathPairFilter(null);
        this._destroy$.next();
        this._destroy$.complete();
    }

    private _updateRouteMode(): void {
        const enabledPathPairs = this._pathPairs.filter(pair => pair.enabled);
        const hasMultipleEnabledPathPairs = enabledPathPairs.length > 1;
        const selectedPathPair = hasMultipleEnabledPathPairs && this._pathPairId != null
            ? enabledPathPairs.find(pair => pair.id === this._pathPairId)
            : null;

        this.showOverview = hasMultipleEnabledPathPairs && selectedPathPair == null;
        this.showDetailView = !this.showOverview;
        this._viewFileFilterService.setPathPairFilter(selectedPathPair != null ? selectedPathPair.id : null);
    }
}
