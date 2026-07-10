import {ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit} from "@angular/core";
import {CommonModule} from "@angular/common";
import {Observable, Subject} from "rxjs";
import {distinctUntilChanged, map, takeUntil} from "rxjs/operators";
import {Modal} from "../../services/utils/modal.service";

import {LoggerService} from "../../services/utils/logger.service";
import {ConfigService} from "../../services/settings/config.service";
import {Config} from "../../services/settings/config";
import {Notification} from "../../services/utils/notification";
import {Localization} from "../../common/localization";
import {NotificationService} from "../../services/utils/notification.service";
import {ServerCommandService} from "../../services/server/server-command.service";
import {
    OPTIONS_CONTEXT_CONNECTIONS, OPTIONS_CONTEXT_DISCOVERY,
    OPTIONS_CONTEXT_OTHER, OPTIONS_CONTEXT_SERVER, OPTIONS_CONTEXT_AUTOQUEUE, OPTIONS_CONTEXT_EXTRACT,
    OPTIONS_CONTEXT_TRANSFER_PROTOCOL, IOption, IOptionsContext
} from "./options-list";
import {ConnectedService} from "../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../services/base/stream-service.registry";
import {ModalAccessibilityService} from "../../services/utils/modal-accessibility.service";
import {PathPairService} from "../../services/settings/path-pair.service";
import {FormsModule} from "@angular/forms";
import {OptionComponent} from "./option.component";
import {AutoQueuePageComponent} from "../autoqueue/autoqueue-page.component";
import {ApiAccessComponent} from "./api-access.component";
import {PathPairsComponent} from "./path-pairs.component";
import {ClickStopPropagationDirective} from "../../common/click-stop-propagation.directive";

@Component({
    selector: "app-settings-page",
    standalone: true,
    imports: [CommonModule, FormsModule, OptionComponent, AutoQueuePageComponent, ApiAccessComponent, PathPairsComponent, ClickStopPropagationDirective],
    templateUrl: "./settings-page.component.html",
    styleUrls: ["./settings-page.component.scss"],
    providers: [],
    changeDetection: ChangeDetectionStrategy.OnPush
})

export class SettingsPageComponent implements OnInit, OnDestroy {
    public serverContext: IOptionsContext = OPTIONS_CONTEXT_SERVER;
    public autoqueueContext: IOptionsContext = OPTIONS_CONTEXT_AUTOQUEUE;
    public OPTIONS_CONTEXT_SERVER = OPTIONS_CONTEXT_SERVER;
    public OPTIONS_CONTEXT_DISCOVERY = OPTIONS_CONTEXT_DISCOVERY;
    public OPTIONS_CONTEXT_CONNECTIONS = OPTIONS_CONTEXT_CONNECTIONS;
    public OPTIONS_CONTEXT_TRANSFER_PROTOCOL = OPTIONS_CONTEXT_TRANSFER_PROTOCOL;
    public OPTIONS_CONTEXT_OTHER = OPTIONS_CONTEXT_OTHER;
    public OPTIONS_CONTEXT_AUTOQUEUE = OPTIONS_CONTEXT_AUTOQUEUE;
    public OPTIONS_CONTEXT_EXTRACT = OPTIONS_CONTEXT_EXTRACT;

    public config: Observable<Config>;

    public commandsEnabled: boolean;

    private _connectedService: ConnectedService;
    private _pathPairService: PathPairService;
    private _destroy$: Subject<void> = new Subject<void>();
    private _destroyed = false;

    private _configRestartNotif: Notification;
    private _configAppliedImmediatelyNotif: Notification;
    private _configAppliedImmediatelyHideTimer: ReturnType<typeof setTimeout> | null = null;
    private _badValueNotifs: Map<string, Notification>;
    private static readonly CONFIG_APPLIED_IMMEDIATELY_HIDE_MS = 7000;
    private static readonly OVERRIDE_NOTE = "Path pairs override this setting when any pair is enabled.";

    constructor(private _logger: LoggerService,
                _streamServiceRegistry: StreamServiceRegistry,
                private _configService: ConfigService,
                pathPairService: PathPairService,
                private _notifService: NotificationService,
                private _commandService: ServerCommandService,
                private _modal: Modal,
                private _modalAccessibility: ModalAccessibilityService,
                private _changeDetector: ChangeDetectorRef) {
        this._connectedService = _streamServiceRegistry.connectedService;
        this._pathPairService = pathPairService;
        this.config = _configService.config;
        this.commandsEnabled = false;
        this._configRestartNotif = new Notification({
            level: Notification.Level.INFO,
            text: Localization.Notification.CONFIG_RESTART
        });
        this._configAppliedImmediatelyNotif = new Notification({
            level: Notification.Level.SUCCESS,
            dismissible: true,
            text: Localization.Notification.CONFIG_APPLIED_IMMEDIATELY
        });
        this._badValueNotifs = new Map();
    }

    // noinspection JSUnusedGlobalSymbols
    ngOnInit() {
        this._connectedService.connected.pipe(takeUntil(this._destroy$)).subscribe({
            next: (connected: boolean) => {
                if (!connected) {
                    // Server went down, hide config status notifications.
                    this._notifService.hide(this._configRestartNotif);
                    this.hideConfigAppliedImmediatelyNotification();
                }

                // Enable/disable commands based on server connection
                this.commandsEnabled = connected;
            }
        });

        this._pathPairService.pathPairs.pipe(
            takeUntil(this._destroy$),
            map((pathPairs) => (pathPairs || []).some((pathPair) => pathPair.enabled)),
            distinctUntilChanged(),
        ).subscribe({
            next: (hasEnabledPairs: boolean) => {
                this.serverContext = SettingsPageComponent.buildServerContext(hasEnabledPairs);
                this.autoqueueContext = SettingsPageComponent.buildAutoqueueContext(hasEnabledPairs);
                this._changeDetector.markForCheck();
            }
        });
    }

    ngOnDestroy() {
        this._destroyed = true;
        this.clearConfigAppliedImmediatelyHideTimer();
        this._destroy$.next();
        this._destroy$.complete();
    }

    onSetConfig(section: string, option: string, value: any) {
        this._configService.set(section, option, value).pipe(takeUntil(this._destroy$)).subscribe({
            next: reaction => {
                const notifKey = section + "." + option;
                if (reaction.success) {
                    this._logger.info(reaction.data);

                    // Hide bad value notification, if any
                    if (this._badValueNotifs.has(notifKey)) {
                        this._notifService.hide(this._badValueNotifs.get(notifKey));
                        this._badValueNotifs.delete(notifKey);
                    }

                    // Only show the restart notification when the backend says this setting needs it.
                    if (this._configService.requiresRestart(section, option)) {
                        this.hideConfigAppliedImmediatelyNotification();
                        this._notifService.show(this._configRestartNotif);
                    } else {
                        this._notifService.hide(this._configRestartNotif);
                        this.showConfigAppliedImmediatelyNotification();
                    }
                } else {
                    this._notifService.hide(this._configRestartNotif);
                    this.hideConfigAppliedImmediatelyNotification();

                    // Show bad value notification
                    const notif = new Notification({
                        level: Notification.Level.DANGER,
                        dismissible: true,
                        text: reaction.errorMessage
                    });
                    if (this._badValueNotifs.has(notifKey)) {
                        this._notifService.hide(this._badValueNotifs.get(notifKey));
                    }
                    this._notifService.show(notif);
                    this._badValueNotifs.set(notifKey, notif);

                    this._logger.error(reaction.errorMessage);
                }
            }
        });
    }

    private showConfigAppliedImmediatelyNotification() {
        this.clearConfigAppliedImmediatelyHideTimer();
        this._notifService.show(this._configAppliedImmediatelyNotif);
        this._configAppliedImmediatelyHideTimer = setTimeout(() => {
            this._configAppliedImmediatelyHideTimer = null;
            this._notifService.hide(this._configAppliedImmediatelyNotif);
        }, SettingsPageComponent.CONFIG_APPLIED_IMMEDIATELY_HIDE_MS);
    }

    private hideConfigAppliedImmediatelyNotification() {
        this.clearConfigAppliedImmediatelyHideTimer();
        this._notifService.hide(this._configAppliedImmediatelyNotif);
    }

    private clearConfigAppliedImmediatelyHideTimer() {
        if (this._configAppliedImmediatelyHideTimer !== null) {
            clearTimeout(this._configAppliedImmediatelyHideTimer);
            this._configAppliedImmediatelyHideTimer = null;
        }
    }

    private static buildServerContext(hasEnabledPairs: boolean): IOptionsContext {
        return {
            ...OPTIONS_CONTEXT_SERVER,
            options: OPTIONS_CONTEXT_SERVER.options.map((option) => {
                if (hasEnabledPairs && (option.valuePath[1] === "remote_path" || option.valuePath[1] === "local_path")) {
                    return { ...option, description: SettingsPageComponent.OVERRIDE_NOTE, disabled: true };
                }
                return option;
            }),
        };
    }

    private static buildAutoqueueContext(hasEnabledPairs: boolean): IOptionsContext {
        return {
            ...OPTIONS_CONTEXT_AUTOQUEUE,
            options: OPTIONS_CONTEXT_AUTOQUEUE.options.map((option) => {
                if (hasEnabledPairs && option.valuePath[1] === "enabled") {
                    return { ...option, description: SettingsPageComponent.OVERRIDE_NOTE, disabled: true };
                }
                return option;
            }),
        };
    }

    isOptionDisabled(option: IOption, config: Config): boolean {
        if (!config) {
            return false;
        }
        const transferBackend = config.getValue("lftp", "transfer_backend");
        return Boolean(
            (option.disabledWhenSftp && config.getValue("lftp", "protocol") === "sftp") ||
            (option.disabledWhenTransferBackend && option.disabledWhenTransferBackend.includes(transferBackend))
        );
    }

    onCommandRestart() {
        const dialogRef = this._modal.confirm()
            .title(Localization.Modal.RESTART_TITLE)
            .okBtn("Restart")
            .okBtnClass("btn btn-primary")
            .cancelBtn("Cancel")
            .cancelBtnClass("btn btn-secondary")
            .isBlocking(false)
            .showClose(false)
            .body(Localization.Modal.RESTART_MESSAGE)
            .open();

        this._modalAccessibility.enhance(dialogRef).then(dRef => {
            if (this._destroyed) {
                return;
            }
            dRef.result.then(
                () => {
                    if (this._destroyed) {
                        return;
                    }
                    this._commandService.restart().pipe(takeUntil(this._destroy$)).subscribe({
                        next: reaction => {
                            if (reaction.success) {
                                this._logger.info(reaction.data);
                            } else {
                                this._logger.error(reaction.errorMessage);
                            }
                        }
                    });
                },
                () => { return; }
            );
        });
    }
}
