import {Injectable, Optional} from "@angular/core";
import {HttpClient, HttpParams} from "@angular/common/http";
import {Observable, Subject, throwError} from "rxjs";
import {map, tap} from "rxjs/operators";

import {BaseStreamService} from "../base/base-stream.service";
import {HistoricalLogResponse, LogRecord} from "./log-record";
import {LoggerService} from "../utils/logger.service";


@Injectable()
export class LogService extends BaseStreamService {
    private static readonly MAX_RETAINED_RECORDS = 20000;

    private _history: LogRecord[] = [];
    private _logs: Subject<LogRecord> = new Subject();

    constructor(private _logger: LoggerService, @Optional() private _http: HttpClient) {
        super();
        this.registerEventName("log-record");
    }

    /**
     * Logs is a hot observable of live appends only.
     * @returns {Observable<LogRecord>}
     */
    get logs(): Observable<LogRecord> {
        return this._logs.asObservable();
    }

    getHistorySnapshot(): LogRecord[] {
        return this._history.slice();
    }

    get maxRetainedRecords(): number {
        return LogService.MAX_RETAINED_RECORDS;
    }

    loadHistory(filters: {text?: string; level?: string; logger?: string; start?: string; end?: string} = {}): Observable<LogRecord[]> {
        if (!this._http) {
            return throwError(() => new Error("Historical log client is unavailable"));
        }
        let params = new HttpParams().set("limit", "500").set("direction", "asc");
        Object.keys(filters).forEach(key => {
            if (filters[key]) {
                params = params.set(key, filters[key]);
            }
        });
        return this._http.get<HistoricalLogResponse>("/server/logs/history/v1", {params}).pipe(
            map(response => response.records.map(item => new LogRecord({
                id: item.id,
                time: new Date(1000 * item.epoch),
                level: LogRecord.Level[item.level],
                loggerName: item.logger,
                message: item.message,
                exceptionTraceback: item.exception
            }))),
            tap(records => this.mergeHistory(records))
        );
    }

    private mergeHistory(records: LogRecord[]) {
        const identities = new Set(this._history.map(record => this.identity(record)));
        records.forEach(record => {
            const identity = this.identity(record);
            if (!identities.has(identity)) {
                identities.add(identity);
                this._history.push(record);
            }
        });
        this._history.sort((left, right) => left.time.getTime() - right.time.getTime());
        this.trimHistory();
    }

    protected onEvent(eventName: string, data: string) {
        try {
            const record = LogRecord.fromJson(JSON.parse(data));
            const identity = this.identity(record);
            if (!record.id || !this._history.some(item => this.identity(item) === identity)) {
                this._history.push(record);
                this.trimHistory();
                this._logs.next(record);
            }
        } catch (error) {
            this._logger.error("Failed to parse log event:", error);
        }
    }

    private identity(record: LogRecord): string {
        return record.id || [record.time.getTime(), record.level, record.loggerName,
            record.message, record.exceptionTraceback].join("\u0000");
    }

    private trimHistory() {
        if (this._history.length > LogService.MAX_RETAINED_RECORDS) {
            this._history.splice(0, this._history.length - LogService.MAX_RETAINED_RECORDS);
        }
    }

    protected onConnected() {
        // nothing to do
    }

    protected onDisconnected() {
        // nothing to do
    }

}
