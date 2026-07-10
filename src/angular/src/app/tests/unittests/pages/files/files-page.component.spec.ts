import {CommonModule} from "@angular/common";
import {Component} from "@angular/core";
import {ComponentFixture, fakeAsync, TestBed, tick} from "@angular/core/testing";
import {BehaviorSubject} from "rxjs";

import {FilesPageComponent} from "../../../../pages/files/files-page.component";
import {ActivatedRoute} from "@angular/router";
import {PathPair, PathPairService} from "../../../../services/settings/path-pair.service";
import {ViewFileFilterService} from "../../../../services/files/view-file-filter.service";


@Component({
    selector: "app-path-pair-stats",
    standalone: true,
    template: ""
})
class StubPathPairStatsComponent {}

@Component({
    selector: "app-file-options",
    standalone: true,
    template: ""
})
class StubFileOptionsComponent {}

@Component({
    selector: "app-file-list",
    standalone: true,
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
            imports: [CommonModule, FilesPageComponent],
            providers: [
                {provide: ActivatedRoute, useClass: MockActivatedRoute},
                {provide: PathPairService, useClass: MockPathPairService},
                {provide: ViewFileFilterService, useClass: MockViewFileFilterService}
            ]
        });
        TestBed.overrideComponent(FilesPageComponent, {
            set: {imports: [CommonModule, StubPathPairStatsComponent, StubFileOptionsComponent, StubFileListComponent]}
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

    it("stays empty on the dashboard until path-pair data resolves, then shows overview", fakeAsync(() => {
        route.setParams({});

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(false);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith(null);
        expect(fixture.nativeElement.querySelector("app-path-pair-stats")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-options")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).toBeNull();

        setTimeout(() => pathPairService.setPathPairs([]), 0);
        tick();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(false);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe(null);

        setTimeout(() => {
            pathPairService.setPathPairs([
                createPathPair("movies", "Movies"),
                createPathPair("tv", "TV")
            ]);
        }, 0);

        tick();

        expect(component.showOverview).toBe(true);
        expect(component.showDetailView).toBe(false);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe(null);
    }));

    it("stays empty on explicit dashboard path-pair routes until path-pair data resolves, then shows the filtered detail view", fakeAsync(() => {
        route.setParams({pathPairId: "movies"});

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(false);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith(null);
        expect(fixture.nativeElement.querySelector("app-path-pair-stats")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-options")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).toBeNull();

        setTimeout(() => pathPairService.setPathPairs([]), 0);
        tick();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(false);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe(null);

        setTimeout(() => {
            pathPairService.setPathPairs([
                createPathPair("movies-id", "Movies"),
                createPathPair("tv-id", "TV")
            ]);
        }, 0);

        tick();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe("movies-id");
    }));

    it("resolves dashboard detail routes from either the slug or the ID", () => {
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies"),
            createPathPair("tv-id", "TV")
        ]);

        route.setParams({pathPairId: "movies"});
        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith("movies-id");
        expect(fixture.nativeElement.querySelector("app-path-pair-stats")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-options")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).not.toBeNull();

        route.setParams({pathPairId: "movies-id"});
        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe("movies-id");
    });

    it("resolves dashboard detail routes when the path-pair ID contains a percent sign", () => {
        route.setParams({pathPairId: "movies%cut"});
        pathPairService.setPathPairs([
            createPathPair("movies%cut", "Movies Cut"),
            createPathPair("tv-id", "TV")
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith("movies%cut");
        expect(fixture.nativeElement.querySelector("app-path-pair-stats")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-options")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).not.toBeNull();
    });

    it("keeps the overview when two enabled path pairs normalize to the same slug", () => {
        route.setParams({pathPairId: "my-movies"});
        pathPairService.setPathPairs([
            createPathPair("movies-one", "My Movies"),
            createPathPair("movies-two", "My-Movies")
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(true);
        expect(component.showDetailView).toBe(false);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith(null);
        expect(fixture.nativeElement.querySelector("app-path-pair-stats")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-options")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).toBeNull();
    });

    it("keeps the original dashboard detail view when only one enabled path pair exists", () => {
        route.setParams({pathPairId: "movies"});
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies")
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith("movies-id");
        expect(fixture.nativeElement.querySelector("app-file-options")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).not.toBeNull();
    });

    it("falls back to the sole enabled path pair when the current route points at a disabled pair", () => {
        route.setParams({pathPairId: "tv"});
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV", false)
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter).toHaveBeenCalledWith("movies");
        expect(fixture.nativeElement.querySelector("app-file-options")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).not.toBeNull();
    });

    it("reconciles a stale path-pair route against the current enabled set at runtime", () => {
        route.setParams({pathPairId: "tv"});
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV", false)
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe("movies");

        pathPairService.setPathPairs([
            createPathPair("movies", "Movies", false),
            createPathPair("tv", "TV")
        ]);

        fixture.detectChanges();

        expect(component.showOverview).toBe(false);
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe("tv");
        expect(fixture.nativeElement.querySelector("app-file-options")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).not.toBeNull();
    });
});
