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

export interface LegacyApiTokenState {
    configured: boolean;
    compatibility_enabled: boolean;
    state: "enabled" | "disabled" | "cleared";
    accepted_for_external_non_admin: boolean;
}

export interface ApiAccessMigrationState {
    legacy_api_token: LegacyApiTokenState;
    api_keys: {
        total: number;
        active: number;
        revoked: number;
    };
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
    private readonly MIGRATION_STATE_URL = "/server/admin/migration/v1";
    private readonly LEGACY_TOKEN_DISABLE_URL = "/server/admin/migration/v1/legacy-api-token/disable";
    private readonly LEGACY_TOKEN_CLEAR_URL = "/server/admin/migration/v1/legacy-api-token/clear";
    private readonly API_KEYS_URL = "/server/admin/api-keys/v1";

    private _migrationState = new BehaviorSubject<ApiAccessMigrationState>(null);
    private _apiKeys = new BehaviorSubject<ApiKeyRecord[]>(null);
    private _includeRevokedApiKeys = false;

    constructor(_streamServiceRegistry: StreamServiceRegistry,
                private _http: HttpClient,
                private _logger: LoggerService) {
        super(_streamServiceRegistry);
    }

    get migrationState(): Observable<ApiAccessMigrationState> {
        return this._migrationState.asObservable();
    }

    get apiKeys(): Observable<ApiKeyRecord[]> {
        return this._apiKeys.asObservable();
    }

    public refresh() {
        this.loadMigrationState();
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

    public getMigrationState(): Observable<ApiAccessMigrationState> {
        return this._http.get<ApiAccessMigrationState>(this.MIGRATION_STATE_URL).pipe(
            map(response => {
                if (response && response.legacy_api_token && response.api_keys) {
                    return response;
                }
                throw new Error("Failed to load API access migration state");
            })
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

    public disableLegacyApiToken(): Observable<ApiAccessMigrationState> {
        return this._http.post<ApiAccessMigrationState>(this.LEGACY_TOKEN_DISABLE_URL, null).pipe(
            tap(response => this._migrationState.next(response)),
            tap(() => this.loadMigrationState(true))
        );
    }

    public clearLegacyApiToken(): Observable<ApiAccessMigrationState> {
        return this._http.post<ApiAccessMigrationState>(this.LEGACY_TOKEN_CLEAR_URL, null).pipe(
            tap(response => this._migrationState.next(response)),
            tap(() => this.loadMigrationState(true))
        );
    }

    protected onConnected() {
        this.refresh();
    }

    protected onDisconnected() {
        this._migrationState.next(null);
        this._apiKeys.next(null);
    }

    private loadMigrationState(preserveCurrentOnError: boolean = false) {
        this.getMigrationState().subscribe({
            next: migrationState => this._migrationState.next(migrationState),
            error: error => {
                this._logger.error(error);
                if (!preserveCurrentOnError) {
                    this._migrationState.next(null);
                }
            }
        });
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
