import {Injectable} from "@angular/core";
import {HttpClient} from "@angular/common/http";
import {Observable} from "rxjs";
import {tap} from "rxjs/operators";
import {ConfigService} from "./config.service";
import {INotifications} from "./config";

export interface NotificationConfigUpdate {
    enabled: boolean;
    provider: "webhook" | "apprise";
    allow_private_networks: boolean;
    download_start: boolean;
    download_complete: boolean;
    extraction_complete: boolean;
    delete_complete: boolean;
    webhook_url?: string;
    hmac_secret?: string;
    apprise_url?: string;
    apprise_tag: string;
}

@Injectable({providedIn: "root"})
export class NotificationsService {
    private readonly CONFIG_URL = "/server/admin/notifications/v1/config";
    private readonly TEST_URL = "/server/admin/notifications/v1/test";

    constructor(private _http: HttpClient, private _configService: ConfigService) {}

    update(value: NotificationConfigUpdate): Observable<{notifications: INotifications}> {
        return this._http.post<{notifications: INotifications}>(this.CONFIG_URL, value).pipe(
            tap(() => this._configService.refresh())
        );
    }

    test(): Observable<{delivered: boolean}> {
        return this._http.post<{delivered: boolean}>(this.TEST_URL, {});
    }
}
