import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import {Observable} from "rxjs/Observable";
import {Subject} from "rxjs/Subject";
import "rxjs/add/operator/takeUntil";

import * as Immutable from "immutable";

import {AutoQueueService} from "../../services/autoqueue/autoqueue.service";
import {AutoQueuePattern} from "../../services/autoqueue/autoqueue-pattern";
import {Notification} from "../../services/utils/notification";
import {NotificationService} from "../../services/utils/notification.service";
import {ConnectedService} from "../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../services/base/stream-service.registry";
import {Config} from "../../services/settings/config";
import {ConfigService} from "../../services/settings/config.service";


@Component({
    selector: "app-autoqueue-page",
    standalone: false,
    templateUrl: "./autoqueue-page.component.html",
    styleUrls: ["./autoqueue-page.component.scss"],
    providers: [],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class AutoQueuePageComponent implements OnInit, OnDestroy {

    public patterns: Observable<Immutable.List<AutoQueuePattern>>;
    public newPattern: string;

    public config: Observable<Config>;

    public connected: boolean;
    public enabled: boolean;
    public patternsOnly: boolean;

    private _connectedService: ConnectedService;
    private _destroy$: Subject<void> = new Subject<void>();

    constructor(private _changeDetector: ChangeDetectorRef,
                private _autoqueueService: AutoQueueService,
                private _notifService: NotificationService,
                private _configService: ConfigService,
                _streamServiceRegistry: StreamServiceRegistry) {
        this._connectedService = _streamServiceRegistry.connectedService;
        this.patterns = _autoqueueService.patterns;
        this.newPattern = "";
        this.connected = false;
        this.enabled = false;
        this.patternsOnly = false;
    }

    // noinspection JSUnusedGlobalSymbols
    ngOnInit() {
        this._connectedService.connected.takeUntil(this._destroy$).subscribe({
            next: (connected: boolean) => {
                this.connected = connected;
                if (!this.connected) {
                    // Clear the input box
                    this.newPattern = "";
                }
            }
        });

        this._configService.config.takeUntil(this._destroy$).subscribe({
            next: config => {
                if(config != null) {
                    this.enabled = config.autoqueue.enabled;
                    this.patternsOnly = config.autoqueue.patterns_only;
                } else {
                    this.enabled = false;
                    this.patternsOnly = false;
                }
                this._changeDetector.detectChanges();
            }
        });
    }

    ngOnDestroy() {
        this._destroy$.next();
        this._destroy$.complete();
    }

    public isPatternEditingEnabled(): boolean {
        return this.connected && this.enabled && this.patternsOnly;
    }

    public canAddPattern(): boolean {
        return this.isPatternEditingEnabled() &&
            this.newPattern != null &&
            this.newPattern.trim().length > 0;
    }

    onAddPattern() {
        if (!this.canAddPattern()) {
            return;
        }

        this._autoqueueService.add(this.newPattern).subscribe({
            next: reaction => {
                if (reaction.success) {
                    // Clear the input box
                    this.newPattern = "";
                } else {
                    // Show dismissible notification
                    const notif = new Notification({
                        level: Notification.Level.DANGER,
                        dismissible: true,
                        text: reaction.errorMessage
                    });
                    this._notifService.show(notif);
                }
            }
        });
    }

    onRemovePattern(pattern: AutoQueuePattern) {
        if (!this.isPatternEditingEnabled() || pattern == null) {
            return;
        }

        this._autoqueueService.remove(pattern.pattern).subscribe({
            next: reaction => {
                if (reaction.success) {
                    // Nothing to do
                } else {
                    // Show dismissible notification
                    const notif = new Notification({
                        level: Notification.Level.DANGER,
                        dismissible: true,
                        text: reaction.errorMessage
                    });
                    this._notifService.show(notif);
                }
            }
        });
    }
}
