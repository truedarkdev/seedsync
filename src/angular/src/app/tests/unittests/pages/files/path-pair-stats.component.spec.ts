import {ComponentFixture, TestBed} from "@angular/core/testing";
import {BehaviorSubject} from "rxjs";

import {FileSizePipe} from "../../../../common/file-size.pipe";
import {PathPairStatsComponent} from "../../../../pages/files/path-pair-stats.component";
import {PathPairService} from "../../../../services/settings/path-pair.service";
import {ModelFileService} from "../../../../services/files/model-file.service";

class MockPathPairService {
    private readonly pairs = new BehaviorSubject<any[]>([]);
    pathPairs$ = this.pairs.asObservable();
    setPathPairs(value: any[]) { this.pairs.next(value); }
}
class MockModelFileService {
    private readonly values = new BehaviorSubject<any[]>([]);
    summaries = this.values.asObservable();
    startSummaryStream = jasmine.createSpy("startSummaryStream");
    stopSummaryStream = jasmine.createSpy("stopSummaryStream");
    refreshSummary = jasmine.createSpy("refreshSummary");
    setSummaries(value: any[]) { this.values.next(value); }
}
const pair = (id: string, name: string) => ({id, name, remote_path: "/r", local_path: "/l", enabled: true, auto_queue: true});

describe("Testing path-pair stats component", () => {
    let fixture: ComponentFixture<PathPairStatsComponent>;
    let pairs: MockPathPairService;
    let model: MockModelFileService;

    beforeEach(() => {
        pairs = new MockPathPairService();
        model = new MockModelFileService();
        TestBed.configureTestingModule({imports: [PathPairStatsComponent, FileSizePipe], providers: [
            {provide: PathPairService, useValue: pairs}, {provide: ModelFileService, useValue: model}
        ]});
        fixture = TestBed.createComponent(PathPairStatsComponent);
    });

    it("uses compact summaries and stays live without file records", () => {
        pairs.setPathPairs([pair("movies", "Movies")]);
        model.setSummaries([{path_pair_id: "movies", root_count: 2, remote_size: 1000, transferred_size: 500,
            local_library_file_count: 10400, local_library_size: 320, local_library_state: "scanning",
            downloading_speed: 250, active_count: 1, queued_count: 0, completed_count: 1}]);
        fixture.detectChanges();
        expect(model.startSummaryStream).toHaveBeenCalled();
        expect(fixture.componentInstance.stats[0].overallProgress).toBe(50);
        expect(fixture.componentInstance.formatLocalFileCount(fixture.componentInstance.stats[0].localFileCount)).toBe("10.4k");
        expect(fixture.nativeElement.textContent).toContain("10.4kfiles");
        expect(fixture.nativeElement.textContent).toContain("320 Blocal");
        expect(fixture.nativeElement.textContent).not.toContain("Local library");
        expect(fixture.nativeElement.textContent).not.toContain("Showing last complete scan");
        expect(fixture.nativeElement.querySelector(".library-values .library-icon")).not.toBeNull();
        expect(fixture.nativeElement.querySelector(".scan-state.scanning .state-dot")).not.toBeNull();

        model.setSummaries([{path_pair_id: "movies", root_count: 2, remote_size: 1000, transferred_size: 1000,
            downloading_speed: 0, active_count: 0, queued_count: 0, completed_count: 2}]);
        expect(fixture.componentInstance.stats[0].overallProgress).toBe(100);
    });

    it("closes the compact summary lifecycle on destroy", () => {
        fixture.detectChanges();
        fixture.destroy();
        expect(model.stopSummaryStream).toHaveBeenCalled();
    });

    it("keeps explicit unknown inventory values unknown rather than displaying zero", () => {
        pairs.setPathPairs([pair("movies", "Movies")]);
        model.setSummaries([{
            path_pair_id: "movies", local_library_file_count: null,
            local_library_size: null, local_library_state: "waiting_for_scan"
        }]);
        fixture.detectChanges();

        const stat = fixture.componentInstance.stats[0];
        expect(stat.localFileCount).toBeNull();
        expect(stat.localLibrarySize).toBeNull();
        expect(fixture.nativeElement.textContent).toContain("Waiting for scan");
        expect(fixture.nativeElement.textContent).toContain("—files");
        expect(fixture.nativeElement.textContent).not.toContain("Showing last complete scan");
    });

    it("keeps a long pair name and scan state inside the responsive card header", () => {
        pairs.setPathPairs([pair("long", "A deliberately long neutral pair name for a narrow dashboard")]);
        model.setSummaries([{
            path_pair_id: "long", local_library_file_count: 1,
            local_library_size: 1, local_library_state: "scanning"
        }]);
        fixture.detectChanges();

        const header = fixture.nativeElement.querySelector(".card-header");
        const name = fixture.nativeElement.querySelector(".pair-name");
        const state = fixture.nativeElement.querySelector(".scan-state.scanning");
        expect(header).not.toBeNull();
        expect(name).not.toBeNull();
        expect(state).not.toBeNull();
        expect(name.textContent).toContain("deliberately long neutral pair name");
    });

    it("renders a fixed-density nine-card overview without scan-detail copy", () => {
        const pathPairs = Array.from({length: 9}, (_, index) => pair(`pair-${index}`, `Pair ${index}`));
        pairs.setPathPairs(pathPairs);
        model.setSummaries(pathPairs.map((pathPair, index) => ({
            path_pair_id: pathPair.id,
            local_library_file_count: index === 0 ? 0 : index * 1000,
            local_library_size: index,
            local_library_state: index === 4 ? "stale" : "up_to_date"
        })));
        fixture.detectChanges();

        expect(fixture.nativeElement.querySelectorAll(".path-pair-card").length).toBe(9);
        expect(fixture.nativeElement.querySelectorAll(".card-header").length).toBe(9);
        expect(fixture.nativeElement.textContent).toContain("0files");
        expect(fixture.nativeElement.textContent).toContain("8kfiles");
        expect(fixture.nativeElement.textContent).not.toContain("Showing last complete scan");
    });
});
