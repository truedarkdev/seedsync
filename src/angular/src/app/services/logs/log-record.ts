import {Record} from "immutable";


/**
 * LogRecord immutable
 */
interface ILogRecord {
    id: string;
    time: Date;
    level: LogRecord.Level;
    loggerName: string;
    message: string;
    exceptionTraceback: string;
}
const DefaultLogRecord: ILogRecord = {
    id: null,
    time: null,
    level: null,
    loggerName: null,
    message: null,
    exceptionTraceback: null,
};
const LogRecordRecord = Record(DefaultLogRecord);
export class LogRecord extends LogRecordRecord implements ILogRecord {
    id: string;
    time: Date;
    level: LogRecord.Level;
    loggerName: string;
    message: string;
    exceptionTraceback: string;

    constructor(props) {
        super(props);
    }
}


export module LogRecord {
    export function fromJson(json: LogRecordJson): LogRecord {
        return new LogRecord({
            id: json.id || null,
            // str -> number, then sec -> ms
            time: new Date(1000 * +json.time),
            level: LogRecord.Level[json.level_name],
            loggerName: json.logger_name,
            message: json.message,
            exceptionTraceback: json.exc_tb
        });
    }

    export enum Level {
        DEBUG       = <any> "DEBUG",
        INFO        = <any> "INFO",
        WARNING     = <any> "WARNING",
        ERROR       = <any> "ERROR",
        CRITICAL    = <any> "CRITICAL",
    }
}


/**
 * LogRecord as serialized by the backend.
 * Note: naming convention matches that used in JSON
 */
export interface LogRecordJson {
    id?: string;
    time: number;
    level_name: string;
    logger_name: string;
    message: string;
    exc_tb: string;
}

export interface HistoricalLogRecordJson {
    id: string;
    epoch: number;
    level: string;
    logger: string;
    message: string;
    exception: string;
}

export interface HistoricalLogResponse {
    schema: string;
    records: HistoricalLogRecordJson[];
    page: {limit: number; direction: string; next_cursor: string; has_more: boolean};
    evidence: {scanned_bytes: number; malformed_records_skipped: number; scan_truncated: boolean};
}
