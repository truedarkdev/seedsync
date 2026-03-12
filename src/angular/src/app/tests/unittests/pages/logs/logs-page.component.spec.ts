import {ComponentFixture, TestBed} from "@angular/core/testing";
import {BehaviorSubject, Subject} from "rxjs/Rx";
import {Observable} from "rxjs/Observable";
import "rxjs/add/observable/of";

import {LogsPageComponent} from "../../../../pages/logs/logs-page.component";
import {LogService} from "../../../../services/logs/log.service";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {DomService} from "../../../../services/utils/dom.service";
import {LogRecord} from "../../../../services/logs/log-record";


class MockLogService {
    private _logs = new Subject<LogRecord>();

    get logs() {
        return this._logs.asObservable();
    }

    push(record: LogRecord) {
        this._logs.next(record);
    }
}

class MockConnectedService {
    connected = new BehaviorSubject(true);
}

class MockDomService {
    headerHeight = Observable.of(0);
}

class MockStreamServiceRegistry {
    logService = TestBed.get(LogService);
    connectedService = TestBed.get(ConnectedService);
}

function createRecord(loggerName: string, message: string, traceback: string = null): LogRecord {
    return new LogRecord({
        time: new Date(0),
        level: LogRecord.Level.INFO,
        loggerName: loggerName,
        message: message,
        exceptionTraceback: traceback
    });
}

describe("Testing logs page component", () => {
    let component: LogsPageComponent;
    let fixture: ComponentFixture<LogsPageComponent>;
    let logService: MockLogService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [LogsPageComponent],
            providers: [
                {provide: LogService, useClass: MockLogService},
                {provide: ConnectedService, useClass: MockConnectedService},
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry},
                {provide: DomService, useClass: MockDomService}
            ]
        });

        fixture = TestBed.createComponent(LogsPageComponent);
        component = fixture.componentInstance;
        logService = TestBed.get(LogService);

        fixture.detectChanges();
    });

    afterEach(() => {
        fixture.destroy();
    });

    it("should filter logs case-insensitively by logger and message text", () => {
        logService.push(createRecord("Downloader", "queued first item"));
        logService.push(createRecord("Scanner", "Remote scan complete"));
        fixture.detectChanges();

        component.onSearchQueryChange("scan");
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(records.length).toBe(1);
        expect(records[0].textContent).toContain("Scanner");

        component.onSearchQueryChange("FIRST");
        fixture.detectChanges();

        const updatedRecords = fixture.nativeElement.querySelectorAll("p.record");
        expect(updatedRecords.length).toBe(1);
        expect(updatedRecords[0].textContent).toContain("queued first item");
    });

    it("should show all visible logs again when the query is cleared", () => {
        logService.push(createRecord("Downloader", "queued first item"));
        logService.push(createRecord("Scanner", "Remote scan complete"));
        fixture.detectChanges();

        component.onSearchQueryChange("scan");
        fixture.detectChanges();
        expect(fixture.nativeElement.querySelectorAll("p.record").length).toBe(1);

        component.onSearchQueryChange("");
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(records.length).toBe(2);
    });

    it("should filter logs from the DOM input using trimmed search text", () => {
        logService.push(createRecord("Downloader", "queued first item"));
        logService.push(createRecord("Scanner", "Remote scan complete"));
        fixture.detectChanges();

        const input = fixture.nativeElement.querySelector("#logs-filter-input");
        input.value = "  scan  ";
        input.dispatchEvent(new Event("input"));
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(component.searchQuery).toBe("  scan  ");
        expect(records.length).toBe(1);
        expect(records[0].textContent).toContain("Scanner");
    });

    it("should match logs by traceback text", () => {
        logService.push(createRecord("Downloader", "queued first item"));
        logService.push(createRecord("Scanner", "Remote scan complete", "Permission denied on remote host"));
        fixture.detectChanges();

        component.onSearchQueryChange("permission denied");
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(records.length).toBe(1);
        expect(records[0].textContent).toContain("Scanner");
    });
});
