import {CommonModule} from "@angular/common";
import {Component} from "@angular/core";
import {ComponentFixture, TestBed} from "@angular/core/testing";
import {BehaviorSubject} from "rxjs/Rx";

import {FilesPageComponent} from "../../../../pages/files/files-page.component";
import {ActivatedRoute} from "@angular/router";
import {PathPair, PathPairService} from "../../../../services/settings/path-pair.service";
import {ViewFileFilterService} from "../../../../services/files/view-file-filter.service";


@Component({
    selector: "app-path-pair-stats",
    template: ""
})
class StubPathPairStatsComponent {}

@Component({
    selector: "app-file-options",
    template: ""
})
class StubFileOptionsComponent {}

@Component({
    selector: "app-file-list",
    template: ""
})
class StubFileListComponent {}

class MockActivatedRoute {
    private readonly _params = new BehaviorSubject<any>({});

    get params() {
        return this._params.asObservable();
    }

    setParams(params: any) {
        this._params.next(params);
    }
}

class MockPathPairService {
    private readonly _pathPairs = new BehaviorSubject<PathPair[]>([]);

    get pathPairs() {
        return this._pathPairs.asObservable();
    }

    setPathPairs(pathPairs: PathPair[]) {
        this._pathPairs.next(pathPairs);
    }
}

class MockViewFileFilterService {
    setPathPairFilter = jasmine.createSpy("setPathPairFilter");
}

function createPathPair(id: string, name: string, enabled = true): PathPair {
    return {
        id: id,
        name: name,
        remote_path: `/remote/${id}`,
        local_path: `/local/${id}`,
        enabled: enabled,
        auto_queue: true
    };
}

describe("Testing files page component", () => {
    let fixture: ComponentFixture<FilesPageComponent>;
    let component: FilesPageComponent;
    let route: MockActivatedRoute;
    let pathPairService: MockPathPairService;
    let viewFileFilterService: MockViewFileFilterService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [
                FilesPageComponent,
                StubPathPairStatsComponent,
                StubFileOptionsComponent,
                StubFileListComponent
            ],
            imports: [CommonModule],
            providers: [
                {provide: ActivatedRoute, useClass: MockActivatedRoute},
                {provide: PathPairService, useClass: MockPathPairService},
                {provide: ViewFileFilterService, useClass: MockViewFileFilterService}
            ]
        });

        fixture = TestBed.createComponent(FilesPageComponent);
        component = fixture.componentInstance;
        route = TestBed.get(ActivatedRoute);
        pathPairService = TestBed.get(PathPairService);
        viewFileFilterService = TestBed.get(ViewFileFilterService);
    });

    afterEach(() => {
        fixture.destroy();
    });

    it("shows overview-only content when multiple enabled path pairs exist", () => {
        route.setParams({});
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV")
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(true);
        expect(component.showDetailView).toBe(false);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith(null);
        expect(fixture.nativeElement.querySelector("app-path-pair-stats")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-options")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).toBeNull();
    });

    it("shows the selected path pair detail view for dashboard path pair routes", () => {
        route.setParams({pathPairId: "movies"});
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV")
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith("movies");
        expect(fixture.nativeElement.querySelector("app-path-pair-stats")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-options")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).not.toBeNull();
    });

    it("keeps the original dashboard detail view when only one enabled path pair exists", () => {
        route.setParams({pathPairId: "movies"});
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies")
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith(null);
        expect(fixture.nativeElement.querySelector("app-file-options")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).not.toBeNull();
    });
});
