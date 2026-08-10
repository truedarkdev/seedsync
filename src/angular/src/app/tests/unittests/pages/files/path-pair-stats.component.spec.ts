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
            downloading_speed: 250, active_count: 1, queued_count: 0, completed_count: 1}]);
        fixture.detectChanges();
        expect(model.startSummaryStream).toHaveBeenCalled();
        expect(fixture.componentInstance.stats[0].overallProgress).toBe(50);

        model.setSummaries([{path_pair_id: "movies", root_count: 2, remote_size: 1000, transferred_size: 1000,
            downloading_speed: 0, active_count: 0, queued_count: 0, completed_count: 2}]);
        expect(fixture.componentInstance.stats[0].overallProgress).toBe(100);
    });

    it("closes the compact summary lifecycle on destroy", () => {
        fixture.detectChanges();
        fixture.destroy();
        expect(model.stopSummaryStream).toHaveBeenCalled();
    });
});
