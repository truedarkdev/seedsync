import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import * as Immutable from "immutable";
import {Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";

import {ViewFileService} from "../../services/files/view-file.service";
import {ViewFile} from "../../services/files/view-file";
import {PathPairService, PathPair} from "../../services/settings/path-pair.service";

export interface PathPairStat {
    pathPairId: string;
    pathPairName: string;
    totalFiles: number;
    downloadingCount: number;
    queuedCount: number;
    downloadedCount: number;
    totalRemoteSize: number;
    totalLocalSize: number;
    totalSpeed: number;
    overallProgress: number;
}

@Component({
    selector: "app-path-pair-stats",
    standalone: false,
    templateUrl: "./path-pair-stats.component.html",
    styleUrls: ["./path-pair-stats.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class PathPairStatsComponent implements OnInit, OnDestroy {
    public stats: PathPairStat[] = [];
    public isExpanded = true;
    public hasMultiplePathPairs = false;

    private readonly _destroy$ = new Subject<void>();
    private _pathPairs: PathPair[] = [];
    private _files: Immutable.List<ViewFile> = Immutable.List<ViewFile>();

    constructor(private _viewFileService: ViewFileService,
                private _pathPairService: PathPairService,
                private _changeDetector: ChangeDetectorRef) {
    }

    ngOnInit(): void {
        this._pathPairService.pathPairs$
            .pipe(takeUntil(this._destroy$))
            .subscribe({
                next: (pathPairs: PathPair[]) => {
                    this._pathPairs = pathPairs || [];
                    this.hasMultiplePathPairs = this._pathPairs.filter(pair => pair.enabled).length > 1;
                    this._updateStats();
                }
            });

        this._viewFileService.files
            .pipe(takeUntil(this._destroy$))
            .subscribe({
                next: (files: Immutable.List<ViewFile>) => {
                    this._files = files || Immutable.List<ViewFile>();
                    this._updateStats();
                }
            });
    }

    ngOnDestroy(): void {
        this._destroy$.next();
        this._destroy$.complete();
    }

    toggleExpanded(): void {
        this.isExpanded = !this.isExpanded;
        this._changeDetector.markForCheck();
    }

    hasActiveTransfers(stat: PathPairStat): boolean {
        return stat.downloadingCount > 0 || stat.queuedCount > 0;
    }

    private _updateStats(): void {
        const enabledPairs = this._pathPairs.filter(pair => pair.enabled);
        if (enabledPairs.length === 0) {
            this.stats = [];
            this._changeDetector.markForCheck();
            return;
        }

        const filesByPathPair: {[key: string]: ViewFile[]} = {};
        enabledPairs.forEach(pair => {
            filesByPathPair[pair.id] = [];
        });

        this._files.forEach((file: ViewFile) => {
            if (file.pathPairId && filesByPathPair[file.pathPairId]) {
                filesByPathPair[file.pathPairId].push(file);
            }
        });

        this.stats = enabledPairs.map(pair => this._buildStat(pair, filesByPathPair[pair.id] || []));
        this._changeDetector.markForCheck();
    }

    private _buildStat(pathPair: PathPair, files: ViewFile[]): PathPairStat {
        let downloadingCount = 0;
        let queuedCount = 0;
        let downloadedCount = 0;
        let totalRemoteSize = 0;
        let totalLocalSize = 0;
        let totalSpeed = 0;

        files.forEach((file: ViewFile) => {
            totalRemoteSize += file.remoteSize || 0;
            totalLocalSize += file.localSize || 0;

            if (file.status === ViewFile.Status.DOWNLOADING) {
                downloadingCount++;
                totalSpeed += file.downloadingSpeed || 0;
            } else if (file.status === ViewFile.Status.QUEUED) {
                queuedCount++;
            } else if (file.status === ViewFile.Status.DOWNLOADED || file.status === ViewFile.Status.EXTRACTED) {
                downloadedCount++;
            }
        });

        return {
            pathPairId: pathPair.id,
            pathPairName: pathPair.name,
            totalFiles: files.length,
            downloadingCount: downloadingCount,
            queuedCount: queuedCount,
            downloadedCount: downloadedCount,
            totalRemoteSize: totalRemoteSize,
            totalLocalSize: totalLocalSize,
            totalSpeed: totalSpeed,
            overallProgress: totalRemoteSize > 0 ? Math.round((totalLocalSize / totalRemoteSize) * 100) : 0
        };
    }
}
