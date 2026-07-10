import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import {CommonModule} from "@angular/common";
import {FormsModule} from "@angular/forms";
import {Observable, Subject} from "rxjs";
import {takeUntil} from "rxjs/operators";

import * as Immutable from "immutable";

import {AutoQueueService} from "../../services/autoqueue/autoqueue.service";
import {AutoQueuePattern} from "../../services/autoqueue/autoqueue-pattern";
import {Notification} from "../../services/utils/notification";
import {NotificationService} from "../../services/utils/notification.service";
import {ConnectedService} from "../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../services/base/stream-service.registry";
import {Config} from "../../services/settings/config";
import {ConfigService} from "../../services/settings/config.service";
import {PathPair, PathPairService} from "../../services/settings/path-pair.service";
import {ClickStopPropagationDirective} from "../../common/click-stop-propagation.directive";


@Component({
    selector: "app-autoqueue-page",
    standalone: true,
    imports: [CommonModule, FormsModule, ClickStopPropagationDirective],
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
    public disabledMessage: string;

    private _connectedService: ConnectedService;
    private _pathPairService: PathPairService;
    private _destroy$: Subject<void> = new Subject<void>();
    private _configEnabled: boolean;
    private _enabledPathPairs: PathPair[];

    constructor(private _changeDetector: ChangeDetectorRef,
                private _autoqueueService: AutoQueueService,
                private _notifService: NotificationService,
                private _configService: ConfigService,
                pathPairService: PathPairService,
                _streamServiceRegistry: StreamServiceRegistry) {
        this._connectedService = _streamServiceRegistry.connectedService;
        this._pathPairService = pathPairService;
        this.patterns = _autoqueueService.patterns;
        this.newPattern = "";
        this.connected = false;
        this.enabled = false;
        this.patternsOnly = false;
        this.disabledMessage = "Auto-Queue is disabled. Enable AutoQueue in Settings to queue files automatically.";
        this._configEnabled = false;
        this._enabledPathPairs = [];
    }

    // noinspection JSUnusedGlobalSymbols
    ngOnInit() {
        this._connectedService.connected.pipe(takeUntil(this._destroy$)).subscribe({
            next: (connected: boolean) => {
                this.connected = connected;
                if (!this.connected) {
                    // Clear the input box
                    this.newPattern = "";
                }
            }
        });

        this._configService.config.pipe(takeUntil(this._destroy$)).subscribe({
            next: config => {
                if(config != null) {
                    this._configEnabled = config.autoqueue.enabled;
                    this.patternsOnly = config.autoqueue.patterns_only;
                } else {
                    this._configEnabled = false;
                    this.patternsOnly = false;
                }
                this._refreshEffectiveAutoqueueState();
            }
        });

        this._pathPairService.pathPairs.pipe(takeUntil(this._destroy$)).subscribe({
            next: pathPairs => {
                this._enabledPathPairs = (pathPairs || []).filter(pair => pair.enabled);
                this._refreshEffectiveAutoqueueState();
            }
        });
    }

    private _refreshEffectiveAutoqueueState() {
        if (this._enabledPathPairs.length > 0) {
            this.enabled = this._enabledPathPairs.some(pair => pair.auto_queue);
            this.disabledMessage = "Auto-Queue is disabled. Enable AutoQueue on a path pair in Settings to queue files automatically.";
        } else {
            this.enabled = this._configEnabled;
            this.disabledMessage = "Auto-Queue is disabled. Enable AutoQueue in Settings to queue files automatically.";
        }
        if (!this._destroy$.isStopped) {
            this._changeDetector.detectChanges();
        }
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
