import {Injectable} from "@angular/core";
import {HttpClient} from "@angular/common/http";
import {BehaviorSubject, Observable} from "rxjs";
import {map, tap} from "rxjs/operators";

import {BaseWebService} from "../base/base-web.service";
import {StreamServiceRegistry} from "../base/stream-service.registry";
import {LoggerService} from "../utils/logger.service";


export interface PathPair {
    id: string;
    name: string;
    remote_path: string;
    local_path: string;
    enabled: boolean;
    auto_queue: boolean;
}

export interface PathPairPayload {
    name: string;
    remote_path: string;
    local_path: string;
    enabled: boolean;
    auto_queue: boolean;
}

interface PathPairResponse {
    success: boolean;
    data?: PathPair | PathPair[];
    error?: string;
    warnings?: string[];
}

export interface PathPairResult {
    pathPair: PathPair;
    warnings: string[];
}


@Injectable()
export class PathPairService extends BaseWebService {
    private readonly PATH_PAIR_URL = "/server/path-pairs";

    private _pathPairs: BehaviorSubject<PathPair[]> = new BehaviorSubject([]);

    constructor(_streamServiceRegistry: StreamServiceRegistry,
                private _http: HttpClient,
                private _logger: LoggerService) {
        super(_streamServiceRegistry);
    }

    get pathPairs(): Observable<PathPair[]> {
        return this._pathPairs.asObservable();
    }

    get pathPairs$(): Observable<PathPair[]> {
        return this.pathPairs;
    }

    refresh() {
        this.getAll().subscribe({
            next: pathPairs => this._pathPairs.next(pathPairs),
            error: error => this._logger.error(error)
        });
    }

    getAll(): Observable<PathPair[]> {
        return this._http.get<PathPairResponse>(this.PATH_PAIR_URL).pipe(
            map(response => {
                if (response.success && Array.isArray(response.data)) {
                    return response.data;
                }
                throw new Error(response.error || "Failed to get path pairs");
            })
        );
    }

    create(pair: PathPairPayload): Observable<PathPairResult> {
        return this._http.post<PathPairResponse>(this.PATH_PAIR_URL, pair).pipe(
            map(response => {
                if (response.success && response.data && !Array.isArray(response.data)) {
                    return {
                        pathPair: response.data,
                        warnings: response.warnings || []
                    };
                }
                throw new Error(response.error || "Failed to create path pair");
            }),
            tap(() => this.refresh())
        );
    }

    update(pair: PathPair): Observable<PathPairResult> {
        return this._http.put<PathPairResponse>(`${this.PATH_PAIR_URL}/${pair.id}`, pair).pipe(
            map(response => {
                if (response.success && response.data && !Array.isArray(response.data)) {
                    return {
                        pathPair: response.data,
                        warnings: response.warnings || []
                    };
                }
                throw new Error(response.error || "Failed to update path pair");
            }),
            tap(() => this.refresh())
        );
    }

    delete(id: string): Observable<void> {
        return this._http.delete<PathPairResponse>(`${this.PATH_PAIR_URL}/${id}`).pipe(
            map(response => {
                if (!response.success) {
                    throw new Error(response.error || "Failed to delete path pair");
                }
                return null;
            }),
            tap(() => this.refresh())
        );
    }

    reorder(ids: string[]): Observable<PathPair[]> {
        return this._http.post<PathPairResponse>(`${this.PATH_PAIR_URL}/reorder`, {order: ids}).pipe(
            map(response => {
                if (response.success && Array.isArray(response.data)) {
                    return response.data;
                }
                throw new Error(response.error || "Failed to reorder path pairs");
            }),
            tap(pathPairs => this._pathPairs.next(pathPairs))
        );
    }

    protected onConnected() {
        this.refresh();
    }

    protected onDisconnected() {
        this._pathPairs.next([]);
    }
}


export let pathPairServiceFactory = (
    _streamServiceRegistry: StreamServiceRegistry,
    _http: HttpClient,
    _logger: LoggerService
) => {
    const pathPairService = new PathPairService(_streamServiceRegistry, _http, _logger);
    pathPairService.onInit();
    return pathPairService;
};

export let PathPairServiceProvider = {
    provide: PathPairService,
    useFactory: pathPairServiceFactory,
    deps: [StreamServiceRegistry, HttpClient, LoggerService]
};
