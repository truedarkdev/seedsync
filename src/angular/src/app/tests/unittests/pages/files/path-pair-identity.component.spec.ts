import {ComponentFixture, TestBed} from "@angular/core/testing";
import {BehaviorSubject} from "rxjs";

import {PathPairIdentityComponent} from "../../../../pages/files/path-pair-identity.component";
import {ModelFileService} from "../../../../services/files/model-file.service";

class MockModelFileService {
    private readonly values = new BehaviorSubject<any[]>([]);
    summaries = this.values.asObservable();
    startSummaryStream = jasmine.createSpy("startSummaryStream");
    stopSummaryStream = jasmine.createSpy("stopSummaryStream");
    setSummaries(value: any[]) { this.values.next(value); }
}

describe("Testing path-pair identity component", () => {
    let fixture: ComponentFixture<PathPairIdentityComponent>;
    let model: MockModelFileService;

    beforeEach(() => {
        model = new MockModelFileService();
        TestBed.configureTestingModule({
            imports: [PathPairIdentityComponent],
            providers: [{provide: ModelFileService, useValue: model}]
        });
        fixture = TestBed.createComponent(PathPairIdentityComponent);
        fixture.componentInstance.pathPair = {
            id: "sample-pair", name: "Sample pair", remote_path: "/remote", local_path: "/local", enabled: true, auto_queue: true
        };
    });

    it("shows the selected pair inventory and retained scan state above filters", () => {
        model.setSummaries([{
            path_pair_id: "sample-pair", local_library_file_count: 10400,
            local_library_size: 320, local_library_state: "scanning"
        }]);
        fixture.detectChanges();

        expect(model.startSummaryStream).toHaveBeenCalled();
        expect(fixture.nativeElement.textContent).toContain("Sample pair");
        expect(fixture.nativeElement.textContent).toContain("10.4k files");
        expect(fixture.nativeElement.textContent).toContain("Scanning");
        expect(fixture.nativeElement.textContent).toContain("Showing last complete scan");
        expect(fixture.nativeElement.querySelector(".scan-status.scanning .state-dot")).not.toBeNull();
    });

    it("resets to the current pair summary without waiting for another SSE event", () => {
        model.setSummaries([
            {path_pair_id: "sample-pair", local_library_file_count: 10, local_library_size: 10, local_library_state: "up_to_date"},
            {path_pair_id: "other-pair", local_library_file_count: 215000, local_library_size: 20, local_library_state: "scanning"}
        ]);
        fixture.detectChanges();

        fixture.componentInstance.pathPair = {
            id: "other-pair", name: "Other pair", remote_path: "/remote-other", local_path: "/local-other", enabled: true, auto_queue: true
        };
        fixture.detectChanges();

        expect(fixture.nativeElement.textContent).toContain("Other pair");
        expect(fixture.nativeElement.textContent).toContain("215k files");
        expect(fixture.nativeElement.textContent).toContain("Scanning");
    });

    it("renders explicit null inventory values as unknown", () => {
        model.setSummaries([{
            path_pair_id: "sample-pair", local_library_file_count: null,
            local_library_size: null, local_library_state: "waiting_for_scan"
        }]);
        fixture.detectChanges();

        expect(fixture.componentInstance.library.fileCount).toBeNull();
        expect(fixture.componentInstance.library.size).toBeNull();
        expect(fixture.nativeElement.textContent).toContain("Waiting for scan");
        expect(fixture.nativeElement.textContent).toContain("— files");
        expect(fixture.nativeElement.textContent).not.toContain("Showing last complete scan");
    });
});
