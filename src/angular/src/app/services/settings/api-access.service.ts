import {Injectable} from "@angular/core";
import {HttpClient, HttpParams} from "@angular/common/http";
import {BehaviorSubject, Observable} from "rxjs";
import {map, tap} from "rxjs/operators";

import {BaseWebService} from "../base/base-web.service";
import {StreamServiceRegistry} from "../base/stream-service.registry";
import {LoggerService} from "../utils/logger.service";


export interface ApiKeyRecord {
    id: string;
    name: string;
    scopes: string[];
    created_at: string;
    updated_at: string;
    revoked_at: string | null;
    active: boolean;
}

interface ApiKeyListResponse {
    keys: ApiKeyRecord[];
}

interface ApiKeyActionResponse {
    key: ApiKeyRecord;
    secret?: string;
}

@Injectable()
export class ApiAccessService extends BaseWebService {
    private readonly FIRST_API_KEY_BOOTSTRAP_URL = "/server/admin/bootstrap/v1/first-api-key";
    private readonly API_KEYS_URL = "/server/admin/api-keys/v1";

    private _apiKeys = new BehaviorSubject<ApiKeyRecord[]>(null);
    private _includeRevokedApiKeys = false;

    constructor(_streamServiceRegistry: StreamServiceRegistry,
                private _http: HttpClient,
                private _logger: LoggerService) {
        super(_streamServiceRegistry);
    }

    get apiKeys(): Observable<ApiKeyRecord[]> {
        return this._apiKeys.asObservable();
    }

    public refresh() {
        this.loadApiKeys();
    }

    public setIncludeRevokedApiKeys(includeRevokedApiKeys: boolean) {
        if (this._includeRevokedApiKeys === includeRevokedApiKeys) {
            return;
        }
        this._includeRevokedApiKeys = includeRevokedApiKeys;
        this.loadApiKeys();
    }

    public listApiKeys(includeRevokedApiKeys: boolean = this._includeRevokedApiKeys): Observable<ApiKeyRecord[]> {
        const params = includeRevokedApiKeys ? new HttpParams().set("include_revoked", "1") : undefined;
        return this._http.get<ApiKeyListResponse>(this.API_KEYS_URL, params ? {params} : undefined).pipe(
            map(response => {
                if (response && Array.isArray(response.keys)) {
                    return response.keys;
                }
                throw new Error("Failed to load API keys");
            })
        );
    }

    public bootstrapFirstApiKey(name: string): Observable<{key: ApiKeyRecord; secret: string}> {
        return this._http.post<ApiKeyActionResponse>(this.FIRST_API_KEY_BOOTSTRAP_URL, {name}).pipe(
            map(response => {
                if (response && response.key && response.secret) {
                    return {
                        key: response.key,
                        secret: response.secret
                    };
                }
                throw new Error("Failed to bootstrap first API key");
            }),
            tap(() => this.refresh())
        );
    }

    public createApiKey(name: string, scopes: string[]): Observable<{key: ApiKeyRecord; secret: string}> {
        return this._http.post<ApiKeyActionResponse>(this.API_KEYS_URL, {name, scopes}).pipe(
            map(response => {
                if (response && response.key && response.secret) {
                    return {
                        key: response.key,
                        secret: response.secret
                    };
                }
                throw new Error("Failed to create API key");
            }),
            tap(() => this.refresh())
        );
    }

    public updateApiKey(keyId: string, name: string, scopes: string[]): Observable<ApiKeyRecord> {
        return this._http.put<ApiKeyActionResponse>(`${this.API_KEYS_URL}/${keyId}`, {name, scopes}).pipe(
            map(response => {
                if (response && response.key) {
                    return response.key;
                }
                throw new Error("Failed to update API key");
            }),
            tap(() => this.refresh())
        );
    }

    public rotateApiKey(keyId: string): Observable<{key: ApiKeyRecord; secret: string}> {
        return this._http.post<ApiKeyActionResponse>(`${this.API_KEYS_URL}/${keyId}/rotate`, null).pipe(
            map(response => {
                if (response && response.key && response.secret) {
                    return {
                        key: response.key,
                        secret: response.secret
                    };
                }
                throw new Error("Failed to rotate API key");
            }),
            tap(() => this.refresh())
        );
    }

    public revokeApiKey(keyId: string): Observable<ApiKeyRecord> {
        return this._http.post<ApiKeyActionResponse>(`${this.API_KEYS_URL}/${keyId}/revoke`, null).pipe(
            map(response => {
                if (response && response.key) {
                    return response.key;
                }
                throw new Error("Failed to revoke API key");
            }),
            tap(() => this.refresh())
        );
    }

    public deleteApiKey(keyId: string): Observable<void> {
        return this._http.delete<void>(`${this.API_KEYS_URL}/${keyId}`).pipe(
            tap(() => this.refresh())
        );
    }

    protected onConnected() {
        this.refresh();
    }

    protected onDisconnected() {
        this._apiKeys.next(null);
    }

    private loadApiKeys() {
        this.listApiKeys().subscribe({
            next: apiKeys => this._apiKeys.next(apiKeys),
            error: error => {
                this._logger.error(error);
                this._apiKeys.next(null);
            }
        });
    }
}

export let apiAccessServiceFactory = (
    _streamServiceRegistry: StreamServiceRegistry,
    _http: HttpClient,
    _logger: LoggerService
) => {
    const apiAccessService = new ApiAccessService(_streamServiceRegistry, _http, _logger);
    apiAccessService.onInit();
    return apiAccessService;
};

export let ApiAccessServiceProvider = {
    provide: ApiAccessService,
    useFactory: apiAccessServiceFactory,
    deps: [StreamServiceRegistry, HttpClient, LoggerService]
};
