import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import {CommonModule} from "@angular/common";
import {Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";

import {PathPairService, PathPair} from "../../services/settings/path-pair.service";
import {FileSizePipe} from "../../common/file-size.pipe";
import {ModelFileService} from "../../services/files/model-file.service";
import {
    formatLocalFileCount, localLibraryStateLabel, localLibrarySummary, LocalLibraryState
} from "./path-pair-local-library";

export interface PathPairStat {
    pathPairId: string;
    pathPairName: string;
    localFileCount: number | null;
    localLibrarySize: number | null;
    localLibraryState: LocalLibraryState;
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
    standalone: true,
    imports: [CommonModule, FileSizePipe],
    templateUrl: "./path-pair-stats.component.html",
    styleUrls: ["./path-pair-stats.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class PathPairStatsComponent implements OnInit, OnDestroy {
    public stats: PathPairStat[] = [];

    private readonly _destroy$ = new Subject<void>();
    private _pathPairs: PathPair[] = [];
    private _summaries: any[] = [];

    constructor(private _pathPairService: PathPairService,
                private _modelFileService: ModelFileService,
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

        this._modelFileService.summaries
            .pipe(takeUntil(this._destroy$))
            .subscribe({
                next: summaries => {
                    // Keep the last successful aggregate through a transient
                    // request failure; an empty response is only accepted as
                    // an explicit successful server answer.
                    this._summaries = summaries || [];
                    this._updateStats();
                }
            });
        this._modelFileService.startSummaryStream();
    }

    ngOnDestroy(): void {
        this._modelFileService.stopSummaryStream();
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

    formatLocalFileCount(count: number | null): string { return formatLocalFileCount(count); }

    localLibraryStateLabel(state: LocalLibraryState): string { return localLibraryStateLabel(state); }

    private _updateStats(): void {
        const enabledPairs = this._pathPairs.filter(pair => pair.enabled);
        if (enabledPairs.length === 0) {
            this.stats = [];
            this._changeDetector.markForCheck();
            return;
        }

        const summariesByPair: {[key: string]: any} = {};
        this._summaries.forEach(summary => summariesByPair[summary.path_pair_id] = summary);
        // Every enabled configured pair keeps its existing card; an absent
        // aggregate is the normal empty-card state and contributes zero.
        this.stats = enabledPairs.map(pair => this._buildStat(pair, summariesByPair[pair.id]));
        this._changeDetector.markForCheck();
    }

    private _buildStat(pathPair: PathPair, summary: any): PathPairStat {
        const current = summary || {};
        const library = localLibrarySummary(current);
        const totalRemoteSize = Number(current.remote_size) || 0;
        const completedSize = Math.min(Math.max(Number(current.transferred_size) || 0, 0), totalRemoteSize);
        const totalSpeed = Number(current.downloading_speed) || 0;
        const remainingSize = Math.max(totalRemoteSize - completedSize, 0);
        const etaSeconds = totalSpeed > 0 && remainingSize > 0 ? Math.ceil(remainingSize / totalSpeed) : null;

        return {
            pathPairId: pathPair.id,
            pathPairName: pathPair.name,
            localFileCount: library.fileCount,
            localLibrarySize: library.size,
            localLibraryState: library.state,
            downloadingCount: Number(current.active_count) || 0,
            queuedCount: Number(current.queued_count) || 0,
            downloadedCount: Number(current.completed_count) || 0,
            totalRemoteSize: totalRemoteSize,
            totalLocalSize: completedSize,
            totalSpeed: totalSpeed,
            etaSeconds: etaSeconds,
            overallProgress: totalRemoteSize > 0 ? Math.round((completedSize / totalRemoteSize) * 100) : 0
        };
    }
}
