import {ComponentFixture, TestBed, fakeAsync, tick} from "@angular/core/testing";
import {BehaviorSubject, Subject, of} from "rxjs";

import {LogsPageComponent} from "../../../../pages/logs/logs-page.component";
import {LogService} from "../../../../services/logs/log.service";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {DomService} from "../../../../services/utils/dom.service";
import {LogRecord} from "../../../../services/logs/log-record";


class MockLogService {
    private _history: LogRecord[] = [];
    private _logs = new Subject<LogRecord>();

    get logs() {
        return this._logs.asObservable();
    }

    get maxRetainedRecords(): number {
        return 20000;
    }

    getHistorySnapshot(): LogRecord[] {
        return this._history.slice();
    }

    push(record: LogRecord) {
        this._history.push(record);
        this._logs.next(record);
    }

    seedHistory(records: LogRecord[]) {
        this._history = records.slice();
    }
}

class MockConnectedService {
    connected = new BehaviorSubject(true);
}

class MockDomService {
    headerHeight = of(0);
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
            imports: [LogsPageComponent],
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

    it("should render the retained history snapshot on first load", () => {
        fixture.destroy();
        logService.seedHistory([
            createRecord("Downloader", "queued first item"),
            createRecord("Scanner", "Remote scan complete")
        ]);

        fixture = TestBed.createComponent(LogsPageComponent);
        component = fixture.componentInstance;
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(records.length).toBe(2);
        expect(records[0].textContent).toContain("Downloader");
        expect(records[1].textContent).toContain("Scanner");
    });

    it("should filter logs case-insensitively by logger and message text", fakeAsync(() => {
        logService.push(createRecord("Downloader", "queued first item"));
        logService.push(createRecord("Scanner", "Remote scan complete"));
        tick(100);
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
    }));

    it("should show all visible logs again when the query is cleared", fakeAsync(() => {
        logService.push(createRecord("Downloader", "queued first item"));
        logService.push(createRecord("Scanner", "Remote scan complete"));
        tick(100);
        fixture.detectChanges();

        component.onSearchQueryChange("scan");
        fixture.detectChanges();
        expect(fixture.nativeElement.querySelectorAll("p.record").length).toBe(1);

        component.onSearchQueryChange("");
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(records.length).toBe(2);
    }));

    it("should filter logs from the DOM input using trimmed search text", fakeAsync(() => {
        logService.push(createRecord("Downloader", "queued first item"));
        logService.push(createRecord("Scanner", "Remote scan complete"));
        tick(100);
        fixture.detectChanges();

        const input = fixture.nativeElement.querySelector("#logs-filter-input");
        input.value = "  scan  ";
        input.dispatchEvent(new Event("input"));
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(component.searchQuery).toBe("  scan  ");
        expect(records.length).toBe(1);
        expect(records[0].textContent).toContain("Scanner");
    }));

    it("should match logs by traceback text", fakeAsync(() => {
        logService.push(createRecord("Downloader", "queued first item"));
        logService.push(createRecord("Scanner", "Remote scan complete", "Permission denied on remote host"));
        tick(100);
        fixture.detectChanges();

        component.onSearchQueryChange("permission denied");
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(records.length).toBe(1);
        expect(records[0].textContent).toContain("Scanner");
    }));

    it("should batch live updates before rerendering", fakeAsync(() => {
        logService.push(createRecord("Downloader", "queued first item"));
        fixture.detectChanges();
        expect(fixture.nativeElement.querySelectorAll("p.record").length).toBe(0);

        logService.push(createRecord("Scanner", "Remote scan complete"));
        fixture.detectChanges();
        expect(fixture.nativeElement.querySelectorAll("p.record").length).toBe(0);

        tick(100);
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(records.length).toBe(2);
        expect(records[0].textContent).toContain("Downloader");
        expect(records[1].textContent).toContain("Scanner");
    }));

    it("should keep search over retained history while rendering only the latest visible window", () => {
        fixture.destroy();

        const retainedRecords: LogRecord[] = [];
        for (let i = 0; i < 1100; i++) {
            retainedRecords.push(createRecord("Logger " + i, "message " + i));
        }
        retainedRecords[0] = createRecord("History hit", "needle");
        retainedRecords[1099] = createRecord("Latest hit", "needle");
        logService.seedHistory(retainedRecords);

        fixture = TestBed.createComponent(LogsPageComponent);
        component = fixture.componentInstance;
        fixture.detectChanges();

        const records = fixture.nativeElement.querySelectorAll("p.record");
        expect(records.length).toBe(1000);
        expect(fixture.nativeElement.textContent).toContain("Showing 1000 of 1100 matching log records");

        component.onSearchQueryChange("needle");
        fixture.detectChanges();

        const filteredRecords = fixture.nativeElement.querySelectorAll("p.record");
        expect(filteredRecords.length).toBe(2);
        expect(filteredRecords[0].textContent).toContain("History hit");
        expect(filteredRecords[1].textContent).toContain("Latest hit");
    });
});
