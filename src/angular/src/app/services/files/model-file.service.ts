import {Injectable, NgZone} from "@angular/core";
import {HttpClient, HttpParams} from "@angular/common/http";
import {BehaviorSubject, Observable, Subscription} from "rxjs";
import * as Immutable from "immutable";

import {LoggerService} from "../utils/logger.service";
import {ModelFile} from "./model-file";
import {RestService, WebReaction} from "../utils/rest.service";

export class ModelEventSourceFactory {
    public static create(url: string): EventSource { return new EventSource(url); }
}

interface ModelPageResponse { records: any[]; next_cursor: string | null; }
interface PendingRecord { file: ModelFile; version: number; }

/** Bounded route-owned root transport; ViewFileService owns all presentation. */
@Injectable()
export class ModelFileService {
    private static readonly TRANSPORT_LIMIT = 200;
    private static readonly MAX_PENDING_PATCHES = 256;
    private static readonly PAGE_SIZES = new Set<number>([25, 50, 100, 500, 1000, 0]);
    private readonly _files = new BehaviorSubject<Immutable.Map<string, ModelFile>>(Immutable.Map());
    private readonly _summary = new BehaviorSubject<any[]>([]);
    private readonly _visibleStateCounts = new BehaviorSubject<{[key: string]: number}>({});
    private _scopeId: string | null = null;
    private _eventSource: EventSource | null = null;
    private _summarySource: EventSource | null = null;
    private _summaryConsumers = 0;
    private _summaryGeneration = 0;
    private _summaryVersion = -1;
    private _request: Subscription | null = null;
    private _generation = 0;
    private _pageSize = 0;
    private _records = Immutable.Map<string, ModelFile>();
    private _recordVersions = Immutable.Map<string, number>();
    private _lastGoodRecords = Immutable.Map<string, ModelFile>();
    private _lastGoodVersions = Immutable.Map<string, number>();
    private _pendingRecords = Immutable.Map<string, PendingRecord>();
    private _pendingRemoved = Immutable.Map<string, number>();
    private _syncing = false;
    private _recoveryAttempts = 0;
    private _retryTimer: any = null;

    constructor(private _logger: LoggerService, private _http: HttpClient,
                private _rest: RestService, private _zone: NgZone) {}

    get files(): Observable<Immutable.Map<string, ModelFile>> { return this._files.asObservable(); }
    get summaries(): Observable<any[]> { return this._summary.asObservable(); }
    get visibleStateCounts(): Observable<{[key: string]: number}> { return this._visibleStateCounts.asObservable(); }
    get isScoped(): boolean { return this._scopeId != null; }

    public setPageSize(size: number): void {
        if (ModelFileService.PAGE_SIZES.has(size)) { this._pageSize = size; }
    }

    public activateScope(scopeId: string): void {
        if (!scopeId || this._scopeId === scopeId) { return; }
        this.deactivateScope();
        this._scopeId = scopeId;
        this._recoveryAttempts = 0;
        this._updateVisibleStateCounts();
        this._openStream(); // model-page atomically subscribes and supplies page one.
    }

    public deactivateScope(): void {
        this._generation++;
        this._request?.unsubscribe();
        this._request = null;
        if (this._retryTimer != null) { clearTimeout(this._retryTimer); this._retryTimer = null; }
        this._eventSource?.close();
        this._eventSource = null;
        this._scopeId = null;
        this._records = Immutable.Map();
        this._recordVersions = Immutable.Map();
        this._lastGoodRecords = Immutable.Map();
        this._lastGoodVersions = Immutable.Map();
        this._pendingRecords = Immutable.Map();
        this._pendingRemoved = Immutable.Map();
        this._syncing = false;
        this._files.next(Immutable.Map());
        this._visibleStateCounts.next({});
    }

    public refreshSummary(): void {
        const generation = ++this._summaryGeneration;
        this._http.get<any>("/server/model/v1/summary").subscribe({
            next: response => {
                if (generation === this._summaryGeneration && this._summarySource == null) { this._setSummaries(response); }
            },
            error: error => this._logger.warn("Unable to refresh model summary", error)
        });
    }

    public startSummaryStream(): void {
        this._summaryConsumers++;
        if (this._summarySource != null) { return; }
        this._summaryGeneration++;
        this._summaryVersion = -1;
        const source = ModelEventSourceFactory.create("/server/model/v1/summary/stream");
        this._summarySource = source;
        source.addEventListener("model-summary", event => this._zone.run(() => {
            if (source !== this._summarySource) { return; }
            try { this._setSummaries(JSON.parse((<MessageEvent>event).data)); }
            catch (error) { this._logger.warn("Ignoring invalid model summary", error); }
        }));
        source.onopen = () => {
            if (source === this._summarySource) {
                // A new connection can follow a backend restart and therefore
                // begin a new model-version epoch.  Do not reset on error:
                // buffered older events from the same stream must not replace
                // the current summary before the reconnect is established.
                this._summaryVersion = -1;
            }
        };
        source.onerror = () => {
            if (source === this._summarySource) {
                this._logger.warn("Model summary stream disconnected");
            }
        };
    }

    public stopSummaryStream(): void {
        this._summaryConsumers = Math.max(0, this._summaryConsumers - 1);
        if (this._summaryConsumers > 0) { return; }
        this._summarySource?.close();
        this._summarySource = null;
        this._summaryGeneration++;
    }

    private static key(file: ModelFile): string { return file.file_id || file.name; }
    private static commandUrl(action: string, file: ModelFile): string {
        let url = "/server/command/" + action + "/" + encodeURIComponent(encodeURIComponent(file.name));
        if (file.file_id) { url += "?file_id=" + encodeURIComponent(file.file_id); }
        return url;
    }
    public queue(file: ModelFile): Observable<WebReaction> { return this._rest.post(ModelFileService.commandUrl("queue", file)); }
    public stop(file: ModelFile): Observable<WebReaction> { return this._rest.post(ModelFileService.commandUrl("stop", file)); }
    public extract(file: ModelFile): Observable<WebReaction> { return this._rest.post(ModelFileService.commandUrl("extract", file)); }
    public deleteLocal(file: ModelFile): Observable<WebReaction> { return this._rest.delete(ModelFileService.commandUrl("delete_local", file)); }
    public deleteRemote(file: ModelFile): Observable<WebReaction> { return this._rest.delete(ModelFileService.commandUrl("delete_remote", file)); }
    public validate(file: ModelFile): Observable<WebReaction> { return this._rest.post(ModelFileService.commandUrl("validate", file)); }
    public retryMove(file: ModelFile): Observable<WebReaction> { return this._rest.post(ModelFileService.commandUrl("retry_move", file)); }

    // Compatibility only: StreamDispatch no longer registers this service.
    public getEventNames(): string[] { return ["model-init", "model-added", "model-updated", "model-removed"]; }
    public notifyConnected(): void {}
    public notifyDisconnected(): void { if (this._scopeId == null) { this._files.next(Immutable.Map()); } }
    public notifyEvent(event: string, data: string): void {
        if (this._scopeId != null) { return; }
        try {
            const parsed = JSON.parse(data);
            let current = this._files.value;
            if (event === "model-init" && Array.isArray(parsed)) {
                current = Immutable.Map<string, ModelFile>(parsed.map(record => {
                    const file = ModelFile.fromJson({...record});
                    return [ModelFileService.key(file), file];
                }));
            } else if ((event === "model-added" || event === "model-updated") && parsed?.new_file) {
                const file = ModelFile.fromJson({...parsed.new_file});
                current = current.set(ModelFileService.key(file), file);
            } else if (event === "model-removed" && parsed?.old_file) {
                const file = ModelFile.fromJson({...parsed.old_file});
                current = current.remove(ModelFileService.key(file));
            } else if (event.indexOf("model-") === 0) {
                this._logger.error("Ignoring invalid legacy model payload");
                return;
            }
            this._files.next(current);
        } catch (error) { this._logger.error("Ignoring invalid legacy model payload", error); }
    }

    private _handleInitialPage(scopeId: string, event: Event): void {
        if (scopeId !== this._scopeId) { return; }
        try {
            const page = JSON.parse((<MessageEvent>event).data) as ModelPageResponse;
            if (!Array.isArray(page.records)) { throw new Error("Initial model page is invalid"); }
            this._generation++;
            this._request?.unsubscribe();
            this._request = null;
            this._syncing = true;
            this._records = this._recordsFrom(page.records, this._version(page));
            this._pendingRecords = Immutable.Map();
            this._pendingRemoved = Immutable.Map();
            if (this._pageSize === 0) { this._publish(); }
            if (page.next_cursor) {
                this._fetchTransportPage(scopeId, this._generation, page.next_cursor);
            } else {
                this._finishSync();
            }
        } catch (error) {
            this._logger.warn("Ignoring invalid scoped model page", error);
            this._scheduleRecovery(error, scopeId, this._generation);
        }
    }

    private _fetchTransportPage(scopeId: string, generation: number, cursor: string): void {
        const params = new HttpParams().set("limit", String(ModelFileService.TRANSPORT_LIMIT)).set("cursor", cursor);
        this._request = this._http.get<ModelPageResponse>(this._rootsUrl(scopeId), {params}).subscribe({
            next: page => {
                if (generation !== this._generation || scopeId !== this._scopeId) { return; }
                if (!Array.isArray(page.records)) {
                    this._scheduleRecovery(new Error("Scoped model continuation is invalid"), scopeId, generation);
                    return;
                }
                this._mergeRecords(page.records, this._version(page));
                if (this._pageSize === 0) { this._publish(); }
                if (page.next_cursor) { this._fetchTransportPage(scopeId, generation, page.next_cursor); }
                else { this._finishSync(); }
            },
            error: error => this._handleCursorError(error, scopeId, generation)
        });
    }

    private _handleCursorError(error: any, scopeId: string, generation: number): void {
        if (generation !== this._generation || scopeId !== this._scopeId) { return; }
        this._scheduleRecovery(error, scopeId, generation);
    }

    private _finishSync(): void {
        this._syncing = false;
        this._applyPendingPatches();
        this._lastGoodRecords = this._records;
        this._lastGoodVersions = this._recordVersions;
        this._recoveryAttempts = 0;
        this._publish();
    }

    private _scheduleRecovery(error: any, scopeId: string, generation: number): void {
        if (generation !== this._generation || scopeId !== this._scopeId) { return; }
        this._syncing = false;
        this._records = this._lastGoodRecords;
        this._recordVersions = this._lastGoodVersions;
        this._pendingRecords = Immutable.Map();
        this._pendingRemoved = Immutable.Map();
        this._publish();
        if (this._recoveryAttempts >= 1) {
            this._logger.warn("Unable to refresh scoped model roots", error);
            return;
        }
        this._recoveryAttempts++;
        this._logger.warn("Retrying scoped model roots after transport failure", error);
        this._retryTimer = setTimeout(() => {
            this._retryTimer = null;
            if (scopeId === this._scopeId) { this._restartStream(); }
        }, 250);
    }

    private _handlePatch(scopeId: string, event: Event): void {
        if (scopeId !== this._scopeId) { return; }
        try {
            const patch = JSON.parse((<MessageEvent>event).data);
            if (!Array.isArray(patch.records) || !Array.isArray(patch.removed_file_ids)) {
                throw new Error("Scoped model patch is missing records or removed_file_ids");
            }
            if (this._syncing) {
                const version = this._version(patch);
                patch.records.forEach(record => this._queueRecordPatch(record, version));
                patch.removed_file_ids.forEach(id => this._queueRemovalPatch(id, version));
                if (this._pendingRecords.size + this._pendingRemoved.size > ModelFileService.MAX_PENDING_PATCHES) {
                    this._restartStream();
                }
                return;
            }
            this._applyPatch(patch.records, patch.removed_file_ids, this._version(patch));
            this._lastGoodRecords = this._records;
            this._lastGoodVersions = this._recordVersions;
            this._publish();
        } catch (error) {
            this._logger.warn("Ignoring invalid scoped model patch", error);
            this._scheduleRecovery(error, scopeId, this._generation);
        }
    }

    private _queueRecordPatch(record: any, version: number): void {
        const file = ModelFile.fromJson({...record});
        const key = ModelFileService.key(file);
        if ((this._pendingRemoved.get(key) || -1) <= version) { this._pendingRemoved = this._pendingRemoved.remove(key); }
        const pending = this._pendingRecords.get(key);
        if (pending == null || pending.version <= version) { this._pendingRecords = this._pendingRecords.set(key, {file, version}); }
    }
    private _queueRemovalPatch(id: any, version: number): void {
        if (typeof id !== "string") { return; }
        const pending = this._pendingRecords.get(id);
        if (pending == null || pending.version <= version) { this._pendingRecords = this._pendingRecords.remove(id); }
        if ((this._pendingRemoved.get(id) || -1) <= version) { this._pendingRemoved = this._pendingRemoved.set(id, version); }
    }
    private _applyPendingPatches(): void {
        this._pendingRecords.forEach((pending, id) => this._applyRecord(id, pending.file, pending.version));
        this._pendingRemoved.forEach((version, id) => {
            if ((this._recordVersions.get(id) || -1) <= version) {
                this._records = this._records.remove(id);
                this._recordVersions = this._recordVersions.set(id, version);
            }
        });
        this._pendingRecords = Immutable.Map();
        this._pendingRemoved = Immutable.Map();
    }
    private _applyPatch(records: any[], removedIds: any[], version: number): void {
        this._mergeRecords(records, version);
        removedIds.forEach(id => {
            if (typeof id === "string" && (this._recordVersions.get(id) || -1) <= version) {
                this._records = this._records.remove(id);
                this._recordVersions = this._recordVersions.set(id, version);
            }
        });
    }
    private _recordsFrom(records: any[], version: number): Immutable.Map<string, ModelFile> {
        let result = Immutable.Map<string, ModelFile>();
        this._recordVersions = Immutable.Map();
        records.forEach(record => {
            const file = ModelFile.fromJson({...record});
            result = result.set(ModelFileService.key(file), file);
            this._recordVersions = this._recordVersions.set(ModelFileService.key(file), version);
        });
        return result;
    }
    private _mergeRecords(records: any[], version: number): void {
        records.forEach(record => {
            const file = ModelFile.fromJson({...record});
            this._applyRecord(ModelFileService.key(file), file, version);
        });
    }
    private _applyRecord(id: string, file: ModelFile, version: number): void {
        if ((this._recordVersions.get(id) || -1) <= version) {
            this._records = this._records.set(id, file);
            this._recordVersions = this._recordVersions.set(id, version);
        }
    }
    private _version(payload: any): number { return typeof payload?.model_version === "number" ? payload.model_version : 0; }
    private _publish(): void { this._files.next(this._records); }

    private _openStream(): void {
        const scopeId = this._scopeId;
        if (scopeId == null) { return; }
        const source = ModelEventSourceFactory.create(this._streamUrl(scopeId) + "?limit=" + ModelFileService.TRANSPORT_LIMIT);
        this._eventSource = source;
        source.addEventListener("model-page", event => this._zone.run(() => {
            if (source === this._eventSource && scopeId === this._scopeId) { this._handleInitialPage(scopeId, event); }
        }));
        source.addEventListener("model-invalidate", event => this._zone.run(() => {
            if (source === this._eventSource && scopeId === this._scopeId) { this._handlePatch(scopeId, event); }
        }));
        source.addEventListener("model-patch", event => this._zone.run(() => {
            if (source === this._eventSource && scopeId === this._scopeId) { this._handlePatch(scopeId, event); }
        }));
        source.addEventListener("model-reset", () => this._zone.run(() => {
            if (source === this._eventSource && scopeId === this._scopeId) { this._restartStream(); }
        }));
        source.onerror = () => {
            if (source === this._eventSource && scopeId === this._scopeId) {
                this._logger.warn("Scoped model stream disconnected", {scopeId});
            }
        };
    }
    private _restartStream(): void {
        if (this._scopeId == null) { return; }
        this._generation++;
        this._request?.unsubscribe();
        this._request = null;
        if (this._retryTimer != null) { clearTimeout(this._retryTimer); this._retryTimer = null; }
        this._syncing = false;
        this._pendingRecords = Immutable.Map();
        this._pendingRemoved = Immutable.Map();
        this._eventSource?.close();
        this._eventSource = null;
        this._openStream();
    }

    private _rootsUrl(scope: string): string { return "/server/model/v1/pairs/" + encodeURIComponent(scope) + "/roots"; }
    private _streamUrl(scope: string): string { return "/server/model/v1/pairs/" + encodeURIComponent(scope) + "/stream"; }
    private _setSummaries(payload: any): void {
        const version = this._version(payload);
        if (version < this._summaryVersion) { return; }
        this._summaryVersion = version;
        const summaries = Array.isArray(payload?.path_pairs) ? payload.path_pairs : Array.isArray(payload) ? payload : [];
        this._summary.next(summaries);
        this._updateVisibleStateCounts();
    }
    private _updateVisibleStateCounts(): void {
        const summary = this._summary.value.find(value => value?.path_pair_id === this._scopeId);
        const counts = summary?.visible_state_counts;
        this._visibleStateCounts.next(counts != null && typeof counts === "object" ? counts : {});
    }
}
