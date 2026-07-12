import {
    AfterContentChecked, AfterViewInit,
    ChangeDetectionStrategy, ChangeDetectorRef, Component, ElementRef, HostListener,
    OnDestroy, OnInit, ViewChild
} from "@angular/core";
import {CommonModule} from "@angular/common";
import {Observable, Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";

import {LogService} from "../../services/logs/log.service";
import {LogRecord} from "../../services/logs/log-record";
import {StreamServiceRegistry} from "../../services/base/stream-service.registry";
import {ConnectedService} from "../../services/utils/connected.service";
import {Localization} from "../../common/localization";
import {DomService} from "../../services/utils/dom.service";

@Component({
    selector: "app-logs-page",
    standalone: true,
    imports: [CommonModule],
    templateUrl: "./logs-page.component.html",
    styleUrls: ["./logs-page.component.scss"],
    providers: [],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class LogsPageComponent implements OnInit, AfterViewInit, AfterContentChecked, OnDestroy {
    public readonly LogRecord = LogRecord;
    public readonly Localization = Localization;
    private static readonly MAX_VISIBLE_RECORDS = 1000;
    private static readonly UPDATE_BATCH_DELAY_MS = 100;

    public headerHeight: Observable<number>;
    public searchQuery = "";
    public levelFilter = "";
    public loggerFilter = "";
    public startFilter = "";
    public endFilter = "";
    public historyLoading = false;
    public historyError = "";
    public visibleRecords: LogRecord[] = [];
    public filteredRecordCount = 0;
    public hiddenRecordCount = 0;

    @ViewChild("logHead") logHead;
    @ViewChild("logTail") logTail;

    public showScrollToTopButton = false;
    public showScrollToBottomButton = false;
    public isConnected = false;
    public hasReceivedLogs = false;

    private _logService: LogService;
    private _connectedService: ConnectedService;
    private _records: LogRecord[] = [];
    private _pendingRecords: LogRecord[] = [];
    private _flushHandle: number = null;
    private _viewInitialized = false;
    private _destroy$: Subject<void> = new Subject<void>();

    constructor(private _elementRef: ElementRef,
                private _changeDetector: ChangeDetectorRef,
                private _streamRegistry: StreamServiceRegistry,
                private _domService: DomService) {
        this._logService = _streamRegistry.logService;
        this._connectedService = _streamRegistry.connectedService;
        this.headerHeight = this._domService.headerHeight;
    }

    ngOnInit() {
        this._records = this._logService.getHistorySnapshot();
        this.hasReceivedLogs = this._records.length > 0;
        this.updateVisibleRecords(false);
        this.loadHistoricalLogs();

        this._connectedService.connected.pipe(takeUntil(this._destroy$)).subscribe({
            next: connected => {
                this.isConnected = connected;
                this._changeDetector.detectChanges();
            }
        });

        this._logService.logs.pipe(takeUntil(this._destroy$)).subscribe({
            next: record => {
                this.enqueueRecord(record);
            }
        });
    }

    ngAfterViewInit() {
        this._viewInitialized = true;
        this.refreshScrollButtonVisibility();
    }

    ngAfterContentChecked() {
        // Refresh button state when tabs is switched away and back
        if (this._viewInitialized) {
            this.refreshScrollButtonVisibility();
        }
    }

    ngOnDestroy() {
        this.clearPendingFlush();
        this._destroy$.next();
        this._destroy$.complete();
    }

    scrollToTop() {
        // this.logHead.nativeElement.scrollIntoView(true);
        window.scrollTo(0, 0);
    }

    scrollToBottom() {
        window.scrollTo(0, document.body.scrollHeight);
    }

    @HostListener("window:scroll", ["$event"])
    checkScroll(_event?: Event) {
        this.refreshScrollButtonVisibility();
    }

    onSearchQueryChange(value: string) {
        this.searchQuery = value || "";
        this.updateVisibleRecords(false);
    }

    loadHistoricalLogs() {
        this.historyLoading = true;
        this.historyError = "";
        this._logService.loadHistory({text: this.searchQuery.trim(), level: this.levelFilter,
            logger: this.loggerFilter.trim(), start: this.toEpoch(this.startFilter),
            end: this.toEpoch(this.endFilter)}).pipe(takeUntil(this._destroy$)).subscribe({
            next: () => {
                this._records = this._logService.getHistorySnapshot();
                this.hasReceivedLogs = this._records.length > 0;
                this.historyLoading = false;
                this.updateVisibleRecords(false);
            },
            error: () => {
                this.historyLoading = false;
                this.historyError = "Historical logs could not be loaded. Live logs remain available.";
                this._changeDetector.detectChanges();
            }
        });
    }

    onLevelFilterChange(value: string) {
        this.levelFilter = value || "";
        this.updateVisibleRecords(false);
    }

    onLoggerFilterChange(value: string) {
        this.loggerFilter = value || "";
        this.updateVisibleRecords(false);
    }

    onTimeFilterChange(start: string, end: string) {
        this.startFilter = start || "";
        this.endFilter = end || "";
        this.updateVisibleRecords(false);
    }

    identifyRecord(_index: number, record: LogRecord): LogRecord {
        return record;
    }

    private enqueueRecord(record: LogRecord) {
        this._pendingRecords.push(record);
        if (this._flushHandle !== null) {
            return;
        }

        this._flushHandle = window.setTimeout(() => {
            this._flushHandle = null;
            this.flushPendingRecords();
        }, LogsPageComponent.UPDATE_BATCH_DELAY_MS);
    }

    private flushPendingRecords() {
        if (this._pendingRecords.length === 0) {
            return;
        }

        // Only auto-follow when the log view is visible and already at the tail.
        const scrollToBottom = this._elementRef.nativeElement.offsetParent != null &&
            this.logTail &&
            LogsPageComponent.isElementInViewport(this.logTail.nativeElement);
        this.hasReceivedLogs = true;
        this._records = this._records.concat(this._pendingRecords);
        this._pendingRecords = [];
        if (this._records.length > this._logService.maxRetainedRecords) {
            this._records.splice(0, this._records.length - this._logService.maxRetainedRecords);
        }
        this.updateVisibleRecords(scrollToBottom);
    }

    private updateVisibleRecords(scrollToBottom: boolean) {
        const filteredRecords = this.getFilteredRecords();
        this.filteredRecordCount = filteredRecords.length;
        this.hiddenRecordCount = Math.max(
            0,
            filteredRecords.length - LogsPageComponent.MAX_VISIBLE_RECORDS
        );
        this.visibleRecords = filteredRecords.slice(-LogsPageComponent.MAX_VISIBLE_RECORDS);
        this._changeDetector.detectChanges();
        if (scrollToBottom) {
            this.scrollToBottom();
        }
        this.refreshScrollButtonVisibility();
    }

    private getFilteredRecords(): LogRecord[] {
        const query = this.searchQuery.trim().toLowerCase();
        return this._records.filter(record => {
            const textMatches = !query || [
                record.loggerName,
                record.message,
                record.exceptionTraceback
            ].some(value => (value || "").toLowerCase().indexOf(query) !== -1);
            return textMatches && (!this.levelFilter || String(record.level) === this.levelFilter) &&
                (!this.loggerFilter.trim() || record.loggerName === this.loggerFilter.trim()) &&
                (!this.startFilter || record.time.getTime() >= new Date(this.startFilter).getTime()) &&
                (!this.endFilter || record.time.getTime() <= new Date(this.endFilter).getTime());
        });
    }

    private toEpoch(value: string): string {
        return value ? String(new Date(value).getTime() / 1000) : "";
    }

    private clearPendingFlush() {
        if (this._flushHandle !== null) {
            clearTimeout(this._flushHandle);
            this._flushHandle = null;
        }
    }

    private refreshScrollButtonVisibility() {
        if (!this.logHead || !this.logTail) {
            return;
        }

        // Show/hide the scroll buttons
        this.showScrollToTopButton = !LogsPageComponent.isElementInViewport(
            this.logHead.nativeElement
        );
        this.showScrollToBottomButton = !LogsPageComponent.isElementInViewport(
            this.logTail.nativeElement
        );
    }

    // Source: https://stackoverflow.com/a/7557433
    private static isElementInViewport(el): boolean {
        const rect = el.getBoundingClientRect();
        return (
            rect.top >= 0 &&
            rect.left >= 0 &&
            rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) && /*or $(window).height() */
            rect.right <= (window.innerWidth || document.documentElement.clientWidth) /*or $(window).width() */
        );
    }
}
