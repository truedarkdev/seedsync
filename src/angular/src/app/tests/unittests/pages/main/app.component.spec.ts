import {NO_ERRORS_SCHEMA} from "@angular/core";
import {ComponentFixture, TestBed} from "@angular/core/testing";
import {NavigationEnd, Router} from "@angular/router";
import {BehaviorSubject, Subject} from "rxjs/Rx";

import {AppComponent} from "../../../../pages/main/app.component";
import {DomService} from "../../../../services/utils/dom.service";
import {PathPair, PathPairService} from "../../../../services/settings/path-pair.service";


class MockRouter {
    public url = "/dashboard";
    public events = new Subject<any>();
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

class MockDomService {
    setHeaderHeight = jasmine.createSpy("setHeaderHeight");
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

describe("Testing app component", () => {
    let fixture: ComponentFixture<AppComponent>;
    let component: AppComponent;
    let router: MockRouter;
    let pathPairService: MockPathPairService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [AppComponent],
            schemas: [NO_ERRORS_SCHEMA],
            providers: [
                {provide: Router, useClass: MockRouter},
                {provide: PathPairService, useClass: MockPathPairService},
                {provide: DomService, useClass: MockDomService}
            ]
        });

        spyOn(window, "scrollTo").and.stub();

        router = TestBed.get(Router);
        pathPairService = TestBed.get(PathPairService);

        fixture = TestBed.createComponent(AppComponent);
        component = fixture.componentInstance;
    });

    afterEach(() => {
        fixture.destroy();
    });

    it("should resolve dashboard detail titles from enabled path pairs and keep static route titles working", () => {
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies"),
            createPathPair("tv", "TV")
        ]);

        router.url = "/dashboard/movies";
        fixture.detectChanges();

        expect(component.activeTitle).toBe("Movies");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Movies");

        router.url = "/dashboard";
        router.events.next(new NavigationEnd(1, "/dashboard", "/dashboard"));
        fixture.detectChanges();

        expect(component.activeTitle).toBe("Dashboard");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Dashboard");

        router.url = "/settings";
        router.events.next(new NavigationEnd(2, "/settings", "/settings"));
        fixture.detectChanges();

        expect(component.activeTitle).toBe("Settings");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Settings");
    });

    it("should keep Dashboard as the title for a dashboard path-pair route when only one enabled path pair exists", () => {
        router.url = "/dashboard/movies";
        pathPairService.setPathPairs([
            createPathPair("movies", "Movies")
        ]);

        fixture.detectChanges();

        expect(component.activeTitle).toBe("Dashboard");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Dashboard");
    });

    it("should fall back to Dashboard when dashboard path-pair title decoding fails", () => {
        router.url = "/dashboard/%E0%A4%A";

        expect(() => {
            pathPairService.setPathPairs([
                createPathPair("movies", "Movies"),
                createPathPair("tv", "TV")
            ]);
            fixture.detectChanges();
        }).not.toThrow();

        expect(component.activeTitle).toBe("Dashboard");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Dashboard");
    });
});
