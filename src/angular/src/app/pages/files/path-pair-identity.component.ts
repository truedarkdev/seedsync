import {ChangeDetectionStrategy, ChangeDetectorRef, Component, Input, OnDestroy, OnInit} from "@angular/core";
import {CommonModule} from "@angular/common";
import {Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";

import {FileSizePipe} from "../../common/file-size.pipe";
import {PathPair} from "../../services/settings/path-pair.service";
import {ModelFileService} from "../../services/files/model-file.service";
import {
    formatLocalFileCount, localLibraryDetail, localLibraryStateLabel, localLibrarySummary, LocalLibrarySummary
} from "./path-pair-local-library";

@Component({
    selector: "app-path-pair-identity",
    standalone: true,
    imports: [CommonModule, FileSizePipe],
    templateUrl: "./path-pair-identity.component.html",
    styleUrls: ["./path-pair-identity.component.scss"],
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class PathPairIdentityComponent implements OnInit, OnDestroy {
    private _pathPair: PathPair;
    private _summaries: any[] = [];

    @Input()
    set pathPair(value: PathPair) {
        this._pathPair = value;
        this._updateLibrary();
    }
    get pathPair(): PathPair { return this._pathPair; }
    public library: LocalLibrarySummary = localLibrarySummary(null);

    private readonly _destroy$ = new Subject<void>();

    constructor(private _modelFileService: ModelFileService, private _changeDetector: ChangeDetectorRef) {}

    ngOnInit(): void {
        this._modelFileService.summaries.pipe(takeUntil(this._destroy$)).subscribe(summaries => {
            this._summaries = summaries || [];
            this._updateLibrary();
        });
        this._modelFileService.startSummaryStream();
    }

    ngOnDestroy(): void {
        this._modelFileService.stopSummaryStream();
        this._destroy$.next();
        this._destroy$.complete();
    }

    formatFileCount(): string { return formatLocalFileCount(this.library.fileCount); }
    stateLabel(): string { return localLibraryStateLabel(this.library.state); }
    detail(): string | null {
        return localLibraryDetail(this.library.state, this.library.fileCount !== null && this.library.size !== null);
    }

    private _updateLibrary(): void {
        const summary = this._summaries.find(value => value?.path_pair_id === this._pathPair?.id);
        this.library = localLibrarySummary(summary);
        this._changeDetector.markForCheck();
    }
}
