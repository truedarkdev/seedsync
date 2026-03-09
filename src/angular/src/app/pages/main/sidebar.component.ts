import {Component, OnInit} from "@angular/core";

import {ROUTE_INFOS} from "../../routes";
import {ServerCommandService} from "../../services/server/server-command.service";
import {LoggerService} from "../../services/utils/logger.service";
import {ConnectedService} from "../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../services/base/stream-service.registry";
import {Notification} from "../../services/utils/notification";
import {NotificationService} from "../../services/utils/notification.service";

@Component({
    selector: "app-sidebar",
    templateUrl: "./sidebar.component.html",
    styleUrls: ["./sidebar.component.scss"]
})

export class SidebarComponent implements OnInit {
    routeInfos = ROUTE_INFOS;

    public commandsEnabled: boolean;

    private _connectedService: ConnectedService;

    constructor(private _logger: LoggerService,
                _streamServiceRegistry: StreamServiceRegistry,
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
