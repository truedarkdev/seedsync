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
    etaSeconds: number | null;
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

    hasActiveTransfers(stat: PathPairStat): boolean {
        return stat.downloadingCount > 0 || stat.queuedCount > 0;
    }

    trackByPathPairId(index: number, stat: PathPairStat): string {
        return stat.pathPairId;
    }

    formatEta(etaSeconds: number | null): string {
        if (etaSeconds === null || !isFinite(etaSeconds) || etaSeconds <= 0) {
            return "";
        }

        const totalSeconds = Math.ceil(etaSeconds);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        if (hours > 0) {
            return `${hours}h ${minutes}m`;
        }

        if (minutes > 0) {
            return `${minutes}m ${seconds}s`;
        }

        return `${seconds}s`;
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
        let completedSize = 0;
        let totalSpeed = 0;

        files.forEach((file: ViewFile) => {
            const remoteSize = file.remoteSize || 0;
            if (remoteSize <= 0) {
                if (file.status === ViewFile.Status.DOWNLOADING) {
                    downloadingCount++;
                    totalSpeed += file.downloadingSpeed || 0;
                } else if (file.status === ViewFile.Status.QUEUED) {
                    queuedCount++;
                } else if (file.status === ViewFile.Status.DOWNLOADED || file.status === ViewFile.Status.EXTRACTED) {
                    downloadedCount++;
                }
                return;
            }

            totalRemoteSize += remoteSize;
            completedSize += Math.min(Math.max(file.transferredSize || 0, 0), remoteSize);

            if (file.status === ViewFile.Status.DOWNLOADING) {
                downloadingCount++;
                totalSpeed += file.downloadingSpeed || 0;
            } else if (file.status === ViewFile.Status.QUEUED) {
                queuedCount++;
            } else if (file.status === ViewFile.Status.DOWNLOADED || file.status === ViewFile.Status.EXTRACTED) {
                downloadedCount++;
            }
        });

        completedSize = totalRemoteSize > 0 ? Math.min(completedSize, totalRemoteSize) : 0;
        const remainingSize = Math.max(totalRemoteSize - completedSize, 0);
        const etaSeconds = totalSpeed > 0 && remainingSize > 0 ? Math.ceil(remainingSize / totalSpeed) : null;

        return {
            pathPairId: pathPair.id,
            pathPairName: pathPair.name,
            totalFiles: files.length,
            downloadingCount: downloadingCount,
            queuedCount: queuedCount,
            downloadedCount: downloadedCount,
            totalRemoteSize: totalRemoteSize,
            totalLocalSize: completedSize,
            totalSpeed: totalSpeed,
            etaSeconds: etaSeconds,
            overallProgress: totalRemoteSize > 0 ? Math.round((completedSize / totalRemoteSize) * 100) : 0
        };
    }
}
