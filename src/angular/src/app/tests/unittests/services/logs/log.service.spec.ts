import {fakeAsync, TestBed, tick} from "@angular/core/testing";

import * as Immutable from "immutable";

import {LoggerService} from "../../../../services/utils/logger.service";
import {LogService} from "../../../../services/logs/log.service";
import {LogRecord} from "../../../../services/logs/log-record";


describe("Testing log service", () => {
    let logService: LogService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            providers: [
                LoggerService,
                LogService
            ]
        });

        logService = TestBed.get(LogService);
    });

    it("should create an instance", () => {
        expect(logService).toBeDefined();
    });

    it("should register all events with the event source", () => {
        expect(logService.getEventNames()).toEqual(
            ["log-record"]
        );
    });

    it("should send correct record on event", fakeAsync(() => {
        let count = 0;
        let latestRecord: LogRecord = null;
        // noinspection JSUnusedAssignment
        let json = null;

        logService.logs.subscribe({
            next: record => {
                count++;
                latestRecord = record;
            }
        });

        json = {
            level_name: "DEBUG",
            time: "1514776875.9439101",
            logger_name: "seedsync.Controller.Model",
            message: "LftpModel: Adding a listener"
        };
        logService.notifyEvent("log-record", JSON.stringify(json));
        tick();
        expect(count).toBe(1);
        expect(Immutable.is(latestRecord, LogRecord.fromJson(json))).toBe(true);

        json = {
            level_name: "WARNING",
            time: "1514771875.9746701",
            logger_name: "another name",
            message: "another message"
        };
        logService.notifyEvent("log-record", JSON.stringify(json));
        tick();
        expect(count).toBe(2);
        expect(Immutable.is(latestRecord, LogRecord.fromJson(json))).toBe(true);
    }));

    it("should retain records in the history snapshot", fakeAsync(() => {
        let count = 0;
        let latestRecord: LogRecord = null;
        // noinspection JSUnusedAssignment
        let data1 = null;
        // noinspection JSUnusedAssignment
        let data2  = null;

        data1 = {
            level_name: "WARNING",
            time: "1514771875.9746701",
            logger_name: "another name",
            message: "another message"
        };
        data2 = {
            level_name: "DEBUG",
            time: "1514776875.9439101",
            logger_name: "seedsync.Controller.Model",
            message: "LftpModel: Adding a listener"
        };

        logService.notifyEvent("log-record", JSON.stringify(data1));
        logService.notifyEvent("log-record", JSON.stringify(data2));
        tick();

        const history = logService.getHistorySnapshot();
        expect(history.length).toBe(2);
        expect(Immutable.is(history[0], LogRecord.fromJson(data1))).toBe(true);
        expect(Immutable.is(history[1], LogRecord.fromJson(data2))).toBe(true);

        logService.logs.subscribe({
            next: record => {
                count++;
                latestRecord = record;
            }
        });
        tick();
        expect(count).toBe(0);

        logService.notifyEvent("log-record", JSON.stringify(data2));
        tick();
        expect(count).toBe(1);
        expect(Immutable.is(latestRecord, LogRecord.fromJson(data2))).toBe(true);

        logService.notifyEvent("log-record", JSON.stringify(data1));
        tick();
        expect(count).toBe(2);
        expect(Immutable.is(latestRecord, LogRecord.fromJson(data1))).toBe(true);
    }));

    it("should return a defensive copy of the retained history", fakeAsync(() => {
        const data = {
            level_name: "WARNING",
            time: "1514771875.9746701",
            logger_name: "another name",
            message: "another message"
        };

        logService.notifyEvent("log-record", JSON.stringify(data));
        tick();

        const history = logService.getHistorySnapshot();
        history.length = 0;

        expect(logService.getHistorySnapshot().length).toBe(1);
    }));

    it("should drop the oldest retained history when the snapshot exceeds the cap", fakeAsync(() => {
        for (let index = 0; index < logService.maxRetainedRecords + 1; index++) {
            logService.notifyEvent("log-record", JSON.stringify({
                level_name: "INFO",
                time: 1514771875 + index,
                logger_name: "logger" + index,
                message: "message " + index
            }));
        }
        tick();

        const history = logService.getHistorySnapshot();
        expect(history.length).toBe(logService.maxRetainedRecords);
        expect(history[0].loggerName).toBe("logger1");
        expect(history[history.length - 1].loggerName).toBe(
            "logger" + logService.maxRetainedRecords
        );
    }));

    it("should ignore malformed log events", fakeAsync(() => {
        let count = 0;

        logService.logs.subscribe({
            next: () => {
                count++;
            }
        });

        spyOn(console, "error");

        logService.notifyEvent("log-record", "{broken json");
        tick();

        expect(count).toBe(0);
        expect(console.error).toHaveBeenCalled();
    }));
});
