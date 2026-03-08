import {
    AfterContentChecked, AfterViewInit,
    ChangeDetectionStrategy, ChangeDetectorRef, Component, ElementRef, HostListener,
    OnDestroy, OnInit, ViewChild, ViewContainerRef
} from "@angular/core";
import {Subject} from "rxjs/Subject";
import "rxjs/add/operator/takeUntil";

import {LogService} from "../../services/logs/log.service";
import {LogRecord} from "../../services/logs/log-record";
import {StreamServiceRegistry} from "../../services/base/stream-service.registry";
import {ConnectedService} from "../../services/utils/connected.service";
import {Localization} from "../../common/localization";
import {DomService} from "../../services/utils/dom.service";
import {Observable} from "rxjs/Observable";

@Component({
    selector: "app-logs-page",
    templateUrl: "./logs-page.component.html",
    styleUrls: ["./logs-page.component.scss"],
    providers: [],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class LogsPageComponent implements OnInit, AfterViewInit, AfterContentChecked, OnDestroy {
    public readonly LogRecord = LogRecord;
    public readonly Localization = Localization;
    private static readonly MAX_LIVE_RECORDS = 500;

    public headerHeight: Observable<number>;

    @ViewChild("templateRecord") templateRecord;

    // Where to insert the cloned content
    @ViewChild("container", {read: ViewContainerRef}) container;

    @ViewChild("logHead") logHead;
    @ViewChild("logTail") logTail;

    public showScrollToTopButton = false;
    public showScrollToBottomButton = false;
    public isConnected = false;
    public hasReceivedLogs = false;

    private _logService: LogService;
    private _connectedService: ConnectedService;
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
        this._connectedService.connected.takeUntil(this._destroy$).subscribe({
            next: connected => {
                this.isConnected = connected;
                this._changeDetector.detectChanges();
            }
        });
    }

    ngAfterViewInit() {
        this._viewInitialized = true;
        this._logService.logs.takeUntil(this._destroy$).subscribe({
            next: record => {
                this.hasReceivedLogs = true;
                this.insertRecord(record);
            }
        });
    }

    ngAfterContentChecked() {
        // Refresh button state when tabs is switched away and back
        if (this._viewInitialized) {
            this.refreshScrollButtonVisibility();
        }
    }

    ngOnDestroy() {
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
    checkScroll() {
        this.refreshScrollButtonVisibility();
    }

    private insertRecord(record: LogRecord) {
        if (!this.container || !this.templateRecord || !this.logTail) {
            return;
        }

        // Scroll down if the log is visible and already scrolled to the bottom
        const scrollToBottom = this._elementRef.nativeElement.offsetParent != null &&
            LogsPageComponent.isElementInViewport(this.logTail.nativeElement);
        this.container.createEmbeddedView(this.templateRecord, {record: record});
        while (this.container.length > LogsPageComponent.MAX_LIVE_RECORDS) {
            this.container.remove(0);
        }
        this._changeDetector.detectChanges();

        if (scrollToBottom) {
            this.scrollToBottom();
        }
        this.refreshScrollButtonVisibility();
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
