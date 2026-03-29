import {Component, OnDestroy, OnInit} from "@angular/core";
import {Subject} from "rxjs/Subject";
import "rxjs/add/operator/takeUntil";

import {ROUTE_INFOS} from "../../routes";
import {ServerCommandService} from "../../services/server/server-command.service";
import {LoggerService} from "../../services/utils/logger.service";
import {ConnectedService} from "../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../services/base/stream-service.registry";
import {Notification} from "../../services/utils/notification";
import {NotificationService} from "../../services/utils/notification.service";
import {PathPair, PathPairService} from "../../services/settings/path-pair.service";

interface SidebarPathPairRoute {
    path: string;
    name: string;
    icon: string;
}

@Component({
    selector: "app-sidebar",
    templateUrl: "./sidebar.component.html",
    styleUrls: ["./sidebar.component.scss"]
})

export class SidebarComponent implements OnInit, OnDestroy {
    dashboardRouteInfo = ROUTE_INFOS.find(value => value.path === "dashboard");
    routeInfos = ROUTE_INFOS.filter(value => value.path !== "dashboard");
    pathPairRoutes: SidebarPathPairRoute[] = [];
    hasMultipleEnabledPathPairs = false;

    public commandsEnabled: boolean;

    private _connectedService: ConnectedService;
    private readonly _destroy$ = new Subject<void>();

    constructor(private _logger: LoggerService,
                _streamServiceRegistry: StreamServiceRegistry,
                private _pathPairService: PathPairService,
                private _commandService: ServerCommandService,
                private _notificationService: NotificationService) {
        this._connectedService = _streamServiceRegistry.connectedService;
        this.commandsEnabled = false;
    }

    // noinspection JSUnusedGlobalSymbols
    ngOnInit() {
        this._connectedService.connected.subscribe({
            next: (connected: boolean) => {
                this.commandsEnabled = connected;
            }
        });

        this._pathPairService.pathPairs
            .takeUntil(this._destroy$)
            .subscribe({
                next: (pathPairs: PathPair[]) => {
                    const enabledPathPairs = (pathPairs || []).filter(pair => pair.enabled);
                    this.hasMultipleEnabledPathPairs = enabledPathPairs.length > 1;
                    this.pathPairRoutes = enabledPathPairs.map(pair => ({
                        path: `dashboard/${encodeURIComponent(pair.id)}`,
                        name: pair.name,
                        icon: "assets/icons/directory.svg"
                    }));
                }
            });
    }

    ngOnDestroy(): void {
        this._destroy$.next();
        this._destroy$.complete();
    }

    onCommandRestart() {
        const restartNotification = new Notification({
            level: Notification.Level.INFO,
            text: "Restarting server...",
            dismissible: false
        });
        this._notificationService.show(restartNotification);

        this._commandService.restart().subscribe({
            next: reaction => {
                this._notificationService.hide(restartNotification);
                if (reaction.success) {
                    this._logger.info(reaction.data);
                    this._notificationService.show(new Notification({
                        level: Notification.Level.SUCCESS,
                        text: "Restart requested. A brief disconnect is expected and the page will reconnect automatically.",
                        dismissible: true
                    }));
                } else {
                    this._logger.error(reaction.errorMessage);
                    this._notificationService.show(new Notification({
                        level: Notification.Level.DANGER,
                        text: `Restart failed: ${reaction.errorMessage}`,
                        dismissible: true
                    }));
                }
            }
        });
    }
}
