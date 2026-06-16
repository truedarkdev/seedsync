import {Injectable} from "@angular/core";
import {HttpClient, HttpErrorResponse} from "@angular/common/http";
import {Observable} from "rxjs";
import {shareReplay} from "rxjs/operators";

import {LoggerService} from "./logger.service";


/**
 * WebReaction encapsulates the response for an action
 * executed on a BaseWebService
 */
export class WebReaction {
    readonly success: boolean;
    readonly data: string;
    readonly errorMessage: string;

    constructor(success: boolean, data: string, errorMessage: string) {
        this.success = success;
        this.data = data;
        this.errorMessage = errorMessage;
    }
}


/**
 * RestService exposes the HTTP REST API to clients
 */
@Injectable()
export class RestService {

    constructor(private _logger: LoggerService,
                private _http: HttpClient) {
    }

    private createReaction(request: Observable<string>, url: string): Observable<WebReaction> {
        return new Observable<WebReaction>(observer => {
            const subscription = request.subscribe(
                data => {
                    this.logResponse(url, data);
                    observer.next(new WebReaction(true, data, null));
                },
                (err: HttpErrorResponse) => {
                    let errorMessage = null;
                    this.logError(url, err);
                    if (err.error instanceof Event) {
                        errorMessage = err.error.type;
                    } else {
                        errorMessage = err.error;
                    }
                    observer.next(new WebReaction(false, null, errorMessage));
                }
            );
            return () => subscription.unsubscribe();
        }).pipe(shareReplay(1));
        // shareReplay is needed to:
        //      prevent duplicate http requests
        //      share result with those that subscribe after the value was published
        // More info: https://blog.thoughtram.io/angular/2016/06/16/cold-vs-hot-observables.html
    }

    private logResponse(url: string, data: string): void {
        if (this.shouldSuppressResponseLog(url)) {
            this._logger.debug("%s http response: %s", url, "[redacted]");
        } else {
            this._logger.debug("%s http response: %s", url, data);
        }
    }

    private logError(url: string, err: HttpErrorResponse): void {
        if (this.shouldSuppressResponseLog(url)) {
            this._logger.debug("%s error: %s", url, "[redacted]");
        } else {
            this._logger.debug("%s error: %O", url, err);
        }
    }

    private shouldSuppressResponseLog(url: string): boolean {
        return url.indexOf("/server/config/") === 0;
    }

    /**
     * Send backend a GET request and generate a WebReaction response
     * @param {string} url
     * @returns {Observable<WebReaction>}
     */
    public sendRequest(url: string): Observable<WebReaction> {
        return this.createReaction(this._http.get(url, {responseType: "text"}), url);
    }

    public post(url: string): Observable<WebReaction> {
        return this.createReaction(this._http.post(url, null, {responseType: "text"}), url);
    }

    public delete(url: string): Observable<WebReaction> {
        return this.createReaction(this._http.delete(url, {responseType: "text"}), url);
    }

    public sendPostRequest(url: string, payload: object): Observable<WebReaction> {
        return this.createReaction(this._http.post(url, payload, {responseType: "text"}), url);
    }
}
