import {Injectable} from "@angular/core";
import {Observable} from "rxjs/Observable";

import {BaseWebService} from "../base/base-web.service";
import {StreamServiceRegistry} from "../base/stream-service.registry";
import {RestService, WebReaction} from "../utils/rest.service";

@Injectable()
export class BulkCommandService extends BaseWebService {
    private readonly BULK_COMMAND_URL = "/server/command/bulk";

    constructor(_streamServiceProvider: StreamServiceRegistry,
                private _restService: RestService) {
        super(_streamServiceProvider);
    }

    public queue(fileNames: string[]): Observable<WebReaction> {
        return this.sendBulkCommand("queue", fileNames);
    }

    public stop(fileNames: string[]): Observable<WebReaction> {
        return this.sendBulkCommand("stop", fileNames);
    }

    public extract(fileNames: string[]): Observable<WebReaction> {
        return this.sendBulkCommand("extract", fileNames);
    }

    public deleteLocal(fileNames: string[]): Observable<WebReaction> {
        return this.sendBulkCommand("delete_local", fileNames);
    }

    public deleteRemote(fileNames: string[]): Observable<WebReaction> {
        return this.sendBulkCommand("delete_remote", fileNames);
    }

    protected onConnected() {
        // Nothing to do
    }

    protected onDisconnected() {
        // Nothing to do
    }

    private sendBulkCommand(action: string, fileNames: string[]): Observable<WebReaction> {
        return this._restService.sendPostRequest(this.BULK_COMMAND_URL + "/" + action, {
            filenames: fileNames
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
