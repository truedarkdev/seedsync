import {Component, OnDestroy, OnInit} from "@angular/core";
import {CommonModule} from "@angular/common";
import {ActivatedRoute} from "@angular/router";
import {Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";

import {PathPair, PathPairService} from "../../services/settings/path-pair.service";
import {resolvePathPairRouteSegment} from "../../services/settings/path-pair-route";
import {ViewFileFilterService} from "../../services/files/view-file-filter.service";
import {ModelFileService} from "../../services/files/model-file.service";
import {FileOptionsComponent} from "./file-options.component";
import {FileListComponent} from "./file-list.component";
import {PathPairStatsComponent} from "./path-pair-stats.component";
import {PathPairIdentityComponent} from "./path-pair-identity.component";

@Component({
    selector: "app-files-page",
    standalone: true,
    imports: [CommonModule, FileOptionsComponent, FileListComponent, PathPairStatsComponent, PathPairIdentityComponent],
    templateUrl: "./files-page.component.html"
})

export class FilesPageComponent implements OnInit, OnDestroy {
    public showOverview = false;
    public showDetailView = false;
    public selectedPathPair: PathPair | null = null;

    private readonly _destroy$ = new Subject<void>();
    private _pathPairs: PathPair[] = [];
    private _pathPairsLoaded = false;
    private _pathPairId: string = null;

    constructor(private _route: ActivatedRoute,
                private _pathPairService: PathPairService,
                private _viewFileFilterService: ViewFileFilterService,
                private _modelFileService: ModelFileService) {
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
                // Legacy test doubles and older hosts do not expose readiness;
                // retain their non-empty signal while real PathPairService can
                // distinguish a completed zero-pair response from startup.
                if ((<any>this._pathPairService).loaded == null && (pathPairs || []).length > 0) {
                    this._pathPairsLoaded = true;
                }
                this._updateRouteMode();
            }
        });
        if ((<any>this._pathPairService).loaded != null) {
            (<any>this._pathPairService).loaded.pipe(takeUntil(this._destroy$)).subscribe((loaded: boolean) => {
                this._pathPairsLoaded = loaded;
                this._updateRouteMode();
            });
        }

        this._updateRouteMode();
    }

    ngOnDestroy(): void {
        this._viewFileFilterService.setPathPairFilter(null);
        this._modelFileService.deactivateScope();
        this._destroy$.next();
        this._destroy$.complete();
    }

    private _updateRouteMode(): void {
        if (!this._pathPairsLoaded) {
            this.showOverview = false;
            this.showDetailView = false;
            this._viewFileFilterService.setPathPairFilter(null);
            this._modelFileService.deactivateScope();
            return;
        }

        const enabledPathPairs = (this._pathPairs || []).filter(pair => pair.enabled);
        const selectedPathPair = this._resolveSelectedPathPair(enabledPathPairs);
        this.selectedPathPair = selectedPathPair;
        const hasMultipleEnabledPathPairs = enabledPathPairs.length > 1;

        this.showOverview = hasMultipleEnabledPathPairs && selectedPathPair == null;
        this.showDetailView = !this.showOverview;
        this._viewFileFilterService.setPathPairFilter(selectedPathPair != null ? selectedPathPair.id : null);
        if (this.showOverview) {
            this._modelFileService.deactivateScope();
            return;
        }
        this._modelFileService.activateScope(selectedPathPair != null ? selectedPathPair.id : "__legacy__");
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
