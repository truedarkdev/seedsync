import {CommonModule} from "@angular/common";
import {Component} from "@angular/core";
import {ComponentFixture, fakeAsync, TestBed, tick} from "@angular/core/testing";
import {BehaviorSubject} from "rxjs";

import {FilesPageComponent} from "../../../../pages/files/files-page.component";
import {ActivatedRoute} from "@angular/router";
import {PathPair, PathPairService} from "../../../../services/settings/path-pair.service";
import {ViewFileFilterService} from "../../../../services/files/view-file-filter.service";
import {ModelFileService} from "../../../../services/files/model-file.service";
import {ViewFileOptionsService} from "../../../../services/files/view-file-options.service";
import {ViewFileOptions} from "../../../../services/files/view-file-options";
import {DomService} from "../../../../services/utils/dom.service";


@Component({
    selector: "app-path-pair-stats",
    standalone: true,
    template: ""
})
class StubPathPairStatsComponent {}

@Component({
    selector: "app-path-pair-identity",
    standalone: true,
    template: ""
})
class StubPathPairIdentityComponent {}

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
    private readonly _loaded = new BehaviorSubject<boolean>(false);

    get pathPairs() {
        return this._pathPairs.asObservable();
    }
    get loaded() { return this._loaded.asObservable(); }

    setPathPairs(pathPairs: PathPair[]) {
        this._pathPairs.next(pathPairs);
        this._loaded.next(true);
    }

    setLoading() {
        this._loaded.next(false);
    }

    disconnect() {
        this._loaded.next(false);
        this._pathPairs.next([]);
    }
}

class MockViewFileFilterService {
    setPathPairFilter = jasmine.createSpy("setPathPairFilter");
}
class MockModelFileService {
    activateScope = jasmine.createSpy("activateScope");
    deactivateScope = jasmine.createSpy("deactivateScope");
    refreshSummary = jasmine.createSpy("refreshSummary");
}
class MockViewFileOptionsService {
    private readonly _options = new BehaviorSubject(new ViewFileOptions({
        showDetails: false,
        sortMethod: ViewFileOptions.SortMethod.SMART_STATUS,
        selectedStatusFilter: null,
        nameFilter: null,
        pinFilter: true
    }));

    get options() { return this._options.asObservable(); }

    setPinned(pinFilter: boolean) {
        this._options.next(new ViewFileOptions(this._options.getValue().set("pinFilter", pinFilter)));
    }
}
class MockDomService {
    private readonly _headerHeight = new BehaviorSubject(0);

    get headerHeight() { return this._headerHeight.asObservable(); }
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
    let modelFileService: MockModelFileService;
    let viewFileOptionsService: MockViewFileOptionsService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            imports: [CommonModule, FilesPageComponent],
            providers: [
                {provide: ActivatedRoute, useClass: MockActivatedRoute},
                {provide: PathPairService, useClass: MockPathPairService},
                {provide: ViewFileFilterService, useClass: MockViewFileFilterService},
                {provide: ModelFileService, useClass: MockModelFileService},
                {provide: ViewFileOptionsService, useClass: MockViewFileOptionsService},
                {provide: DomService, useClass: MockDomService}
            ]
        });
        TestBed.overrideComponent(FilesPageComponent, {
            set: {imports: [
                CommonModule, StubPathPairStatsComponent, StubPathPairIdentityComponent,
                StubFileOptionsComponent, StubFileListComponent
            ]}
        });

        fixture = TestBed.createComponent(FilesPageComponent);
        component = fixture.componentInstance;
        route = TestBed.get(ActivatedRoute);
        pathPairService = TestBed.get(PathPairService);
        viewFileFilterService = TestBed.get(ViewFileFilterService);
        modelFileService = TestBed.get(ModelFileService);
        viewFileOptionsService = TestBed.get(ViewFileOptionsService) as any;
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
        expect(modelFileService.activateScope).not.toHaveBeenCalled();
        expect(modelFileService.deactivateScope).toHaveBeenCalled();
        expect(fixture.nativeElement.querySelector("app-path-pair-stats")).not.toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-options")).toBeNull();
        expect(fixture.nativeElement.querySelector("app-file-list")).toBeNull();
    });

    it("groups selected-pair identity and controls in the selected context workspace", () => {
        route.setParams({pathPairId: "movies"});
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV")
        ]);

        fixture.detectChanges();

        const workspace = fixture.nativeElement.querySelector(".selected-pair-workspace.has-selected-pair");
        const header = workspace.querySelector(".selected-pair-header");
        expect(workspace).not.toBeNull();
        expect(header).not.toBeNull();
        expect(header.classList.contains("pinned")).toBe(true);
        expect(header.querySelector("app-path-pair-identity")).not.toBeNull();
        expect(header.querySelector("app-file-options.selected-context")).not.toBeNull();
        const fileList = workspace.querySelector("app-file-list.selected-context");
        expect(fileList).not.toBeNull();
        expect(header.contains(fileList)).toBe(false);

        viewFileOptionsService.setPinned(false);
        fixture.detectChanges();
        expect(header.classList.contains("pinned")).toBe(false);
    });

    it("pins the legacy detail toolbar when no enabled path pair exists", () => {
        route.setParams({});
        pathPairService.setPathPairs([]);

        fixture.detectChanges();

        expect(component.showDetailView).toBe(true);
        expect(modelFileService.activateScope).toHaveBeenCalledWith("__legacy__");
        const header = fixture.nativeElement.querySelector(".selected-pair-header");
        expect(header).not.toBeNull();
        expect(header.classList.contains("pinned")).toBe(true);
        expect(header.querySelector("app-path-pair-identity")).toBeNull();
        expect(header.querySelector("app-file-options.selected-context")).toBeNull();
    });

    it("does not activate the legacy scope for a cold pair deep link before readiness", () => {
        fixture.detectChanges();
        route.setParams({pathPairId: "movies"});

        expect(modelFileService.activateScope).not.toHaveBeenCalled();

        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV")
        ]);
        expect(modelFileService.activateScope).toHaveBeenCalledTimes(1);
        expect(modelFileService.activateScope).toHaveBeenCalledWith("movies");
        expect(modelFileService.activateScope).not.toHaveBeenCalledWith("__legacy__");
    });

    it("does not transiently activate the legacy scope when path-pair connection closes", () => {
        fixture.detectChanges();
        route.setParams({pathPairId: "movies"});
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV")
        ]);
        expect(modelFileService.activateScope).toHaveBeenCalledWith("movies");

        pathPairService.disconnect();

        expect(modelFileService.activateScope).not.toHaveBeenCalledWith("__legacy__");
        expect(modelFileService.deactivateScope).toHaveBeenCalled();
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
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe(null);
        expect(modelFileService.activateScope).toHaveBeenCalledWith("__legacy__");

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
        expect(modelFileService.activateScope).toHaveBeenCalledWith("__legacy__");
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
        expect(component.showDetailView).toBe(true);
        expect(viewFileFilterService.setPathPairFilter.calls.mostRecent().args[0]).toBe(null);
        expect(modelFileService.activateScope).toHaveBeenCalledWith("__legacy__");

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
        expect(fixture.nativeElement.querySelector("app-path-pair-identity")).not.toBeNull();
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
