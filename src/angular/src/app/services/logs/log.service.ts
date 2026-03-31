import {Injectable} from "@angular/core";
import {Observable, Subject} from "rxjs";

import {BaseStreamService} from "../base/base-stream.service";
import {LogRecord} from "./log-record";
import {LoggerService} from "../utils/logger.service";


@Injectable()
export class LogService extends BaseStreamService {
    private static readonly MAX_RETAINED_RECORDS = 20000;

    private _history: LogRecord[] = [];
    private _logs: Subject<LogRecord> = new Subject();

    constructor(private _logger: LoggerService) {
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

    protected onEvent(eventName: string, data: string) {
        try {
            const record = LogRecord.fromJson(JSON.parse(data));
            this._history.push(record);
            if (this._history.length > LogService.MAX_RETAINED_RECORDS) {
                this._history.splice(0, this._history.length - LogService.MAX_RETAINED_RECORDS);
            }
            this._logs.next(record);
        } catch (error) {
            this._logger.error("Failed to parse log event:", error);
        }
    }

    protected onConnected() {
        // nothing to do
    }

    protected onDisconnected() {
        // nothing to do
    }

}
