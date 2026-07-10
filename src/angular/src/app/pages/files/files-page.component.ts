import {Component, OnDestroy, OnInit} from "@angular/core";
import {CommonModule} from "@angular/common";
import {ActivatedRoute} from "@angular/router";
import {Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";

import {PathPair, PathPairService} from "../../services/settings/path-pair.service";
import {resolvePathPairRouteSegment} from "../../services/settings/path-pair-route";
import {ViewFileFilterService} from "../../services/files/view-file-filter.service";
import {FileOptionsComponent} from "./file-options.component";
import {FileListComponent} from "./file-list.component";
import {PathPairStatsComponent} from "./path-pair-stats.component";

@Component({
    selector: "app-files-page",
    standalone: true,
    imports: [CommonModule, FileOptionsComponent, FileListComponent, PathPairStatsComponent],
    templateUrl: "./files-page.component.html"
})

export class FilesPageComponent implements OnInit, OnDestroy {
    public showOverview = false;
    public showDetailView = false;

    private readonly _destroy$ = new Subject<void>();
    private _pathPairs: PathPair[] = [];
    private _pathPairsLoaded = false;
    private _pathPairId: string = null;

    constructor(private _route: ActivatedRoute,
                private _pathPairService: PathPairService,
                private _viewFileFilterService: ViewFileFilterService) {
    }

    ngOnInit(): void {
        this._route.params.pipe(takeUntil(this._destroy$)).subscribe({
            next: params => {
                this._pathPairId = params["pathPairId"] || null;
                this._updateRouteMode();
            }
        });

        this._pathPairService.pathPairs.pipe(takeUntil(this._destroy$)).subscribe({
            next: (pathPairs: PathPair[]) => {
                this._pathPairs = pathPairs;
                if ((this._pathPairs || []).length > 0) {
                    this._pathPairsLoaded = true;
                }
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
        if (!this._pathPairsLoaded) {
            this.showOverview = false;
            this.showDetailView = false;
            this._viewFileFilterService.setPathPairFilter(null);
            return;
        }

        const enabledPathPairs = (this._pathPairs || []).filter(pair => pair.enabled);
        const selectedPathPair = this._resolveSelectedPathPair(enabledPathPairs);
        const hasMultipleEnabledPathPairs = enabledPathPairs.length > 1;

        this.showOverview = hasMultipleEnabledPathPairs && selectedPathPair == null;
        this.showDetailView = !this.showOverview;
        this._viewFileFilterService.setPathPairFilter(selectedPathPair != null ? selectedPathPair.id : null);
    }

    private _resolveSelectedPathPair(enabledPathPairs: PathPair[]): PathPair {
        if (enabledPathPairs.length === 1) {
            return enabledPathPairs[0];
        }

        if (this._pathPairId != null) {
            const pathPairRouteMatch = resolvePathPairRouteSegment(this._pathPairId, enabledPathPairs);
            if (pathPairRouteMatch.type === "id" || pathPairRouteMatch.type === "slug") {
                return pathPairRouteMatch.pathPair;
            }

            if (pathPairRouteMatch.type === "ambiguous") {
                return null;
            }

            return enabledPathPairs[0] || null;
        }

        return null;
    }
}
