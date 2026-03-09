import {Injectable} from "@angular/core";
import {Observable} from "rxjs/Observable";

import {BaseWebService} from "../base/base-web.service";
import {StreamServiceRegistry} from "../base/stream-service.registry";
import {RestService, WebReaction} from "../utils/rest.service";

export interface BulkCommandFile {
    file_id: string;
    name: string;
}

@Injectable()
export class BulkCommandService extends BaseWebService {
    private readonly BULK_COMMAND_URL = "/server/command/bulk";

    constructor(_streamServiceProvider: StreamServiceRegistry,
                private _restService: RestService) {
        super(_streamServiceProvider);
    }

    public queue(files: BulkCommandFile[]): Observable<WebReaction> {
        return this.sendBulkCommand("queue", files);
    }

    public stop(files: BulkCommandFile[]): Observable<WebReaction> {
        return this.sendBulkCommand("stop", files);
    }

    public extract(files: BulkCommandFile[]): Observable<WebReaction> {
        return this.sendBulkCommand("extract", files);
    }

    public deleteLocal(files: BulkCommandFile[]): Observable<WebReaction> {
        return this.sendBulkCommand("delete_local", files);
    }

    public deleteRemote(files: BulkCommandFile[]): Observable<WebReaction> {
        return this.sendBulkCommand("delete_remote", files);
    }

    protected onConnected() {
        // Nothing to do
    }

    protected onDisconnected() {
        // Nothing to do
    }

    private sendBulkCommand(action: string, files: BulkCommandFile[]): Observable<WebReaction> {
        return this._restService.sendPostRequest(this.BULK_COMMAND_URL + "/" + action, {
            files: files
        });
    }
}

export let bulkCommandServiceFactory = (
    _streamServiceRegistry: StreamServiceRegistry,
    _restService: RestService
) => {
    const bulkCommandService = new BulkCommandService(_streamServiceRegistry, _restService);
    bulkCommandService.onInit();
    return bulkCommandService;
};

export let BulkCommandServiceProvider = {
    provide: BulkCommandService,
    useFactory: bulkCommandServiceFactory,
    deps: [StreamServiceRegistry, RestService]
};
