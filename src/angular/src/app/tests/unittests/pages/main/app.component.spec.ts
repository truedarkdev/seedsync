import {NO_ERRORS_SCHEMA} from "@angular/core";
import {CommonModule} from "@angular/common";
import {RouterOutlet} from "@angular/router";
import {ComponentFixture, TestBed} from "@angular/core/testing";
import {NavigationEnd, Router} from "@angular/router";
import {BehaviorSubject, Subject} from "rxjs";

import {AppComponent} from "../../../../pages/main/app.component";
import {DomService} from "../../../../services/utils/dom.service";
import {PathPair, PathPairService} from "../../../../services/settings/path-pair.service";

declare function require(moduleName: string): any;
const {version: appVersion} = require("../../../../../../package.json");


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
            imports: [AppComponent],
            schemas: [NO_ERRORS_SCHEMA],
            providers: [
                {provide: Router, useClass: MockRouter},
                {provide: PathPairService, useClass: MockPathPairService},
                {provide: DomService, useClass: MockDomService}
            ]
        });
        TestBed.overrideComponent(AppComponent, {
            set: {imports: [CommonModule, RouterOutlet]}
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

    function detectSettledChanges() {
        fixture.detectChanges();
        fixture.detectChanges();
    }

    it("should resolve the dashboard detail title from the path-pair slug", () => {
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies"),
            createPathPair("tv-id", "TV")
        ]);

        router.url = "/dashboard/movies";
        detectSettledChanges();

        expect(component.activeTitle).toBe("Movies");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Movies");
    });

    it("should render the authoritative build version in the sidebar footer", () => {
        detectSettledChanges();

        const version = fixture.nativeElement.querySelector("#sidebar-version");
        expect(version).not.toBeNull();
        expect(version.textContent).toContain("CURRENT VERSION");
        expect(version.textContent).toContain(`v${appVersion}`);
        expect(version.getAttribute("aria-label")).toBe(`SeedSync current version ${appVersion}`);
        expect(version.querySelector(".version-label").textContent.trim()).toBe("CURRENT VERSION");
        expect(version.querySelector(".version-value").textContent.trim()).toBe(`v${appVersion}`);
        expect(version.querySelector(".signature-mark")).toBeNull();
        expect(version.querySelector(".signature-brand")).toBeNull();
    });

    it("should resolve the dashboard detail title from the path-pair ID", () => {
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies"),
            createPathPair("tv-id", "TV")
        ]);

        router.url = "/dashboard/movies-id";
        router.events.next(new NavigationEnd(1, "/dashboard/movies-id", "/dashboard/movies-id"));
        detectSettledChanges();

        expect(component.activeTitle).toBe("Movies");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Movies");
    });

    it("should keep Dashboard as the title for the dashboard root route", () => {
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies"),
            createPathPair("tv-id", "TV")
        ]);

        router.url = "/dashboard";
        router.events.next(new NavigationEnd(1, "/dashboard", "/dashboard"));
        detectSettledChanges();

        expect(component.activeTitle).toBe("Dashboard");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Dashboard");
    });

    it("should retain the mobile SeedSync mark beside the route title", () => {
        detectSettledChanges();

        const brand = fixture.nativeElement.querySelector(".mobile-brand");
        expect(brand).not.toBeNull();
        expect(brand.querySelector("img").getAttribute("src")).toBe("assets/logo.png");
        expect(brand.textContent).toContain("SeedSync");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Dashboard");
    });

    it("should keep static route titles working after dashboard detail routes", () => {
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies"),
            createPathPair("tv-id", "TV")
        ]);

        router.url = "/settings";
        router.events.next(new NavigationEnd(2, "/settings", "/settings"));
        detectSettledChanges();

        expect(component.activeTitle).toBe("Settings");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Settings");
    });

    it("should keep Dashboard as the title for a dashboard path-pair route when only one enabled path pair exists", () => {
        router.url = "/dashboard/movies";
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies")
        ]);

        detectSettledChanges();

        expect(component.activeTitle).toBe("Dashboard");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Dashboard");
    });

    it("should fall back to Dashboard when dashboard path-pair title decoding fails", () => {
        router.url = "/dashboard/%E0%A4%A";

        expect(() => {
            pathPairService.setPathPairs([
                createPathPair("movies-id", "Movies"),
                createPathPair("tv-id", "TV")
            ]);
            detectSettledChanges();
        }).not.toThrow();

        expect(component.activeTitle).toBe("Dashboard");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Dashboard");
    });

    it("should keep Dashboard as the title when two enabled path pairs normalize to the same slug", () => {
        router.url = "/dashboard/my-movies";
        pathPairService.setPathPairs([
            createPathPair("movies-one", "My Movies"),
            createPathPair("movies-two", "My-Movies")
        ]);

        detectSettledChanges();

        expect(component.activeTitle).toBe("Dashboard");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Dashboard");
    });

    it("should resolve dashboard detail titles when the path-pair ID contains a percent sign", () => {
        router.url = "/dashboard/movies%25cut";
        pathPairService.setPathPairs([
            createPathPair("movies%cut", "Movies Cut"),
            createPathPair("tv-id", "TV")
        ]);

        detectSettledChanges();

        expect(component.activeTitle).toBe("Movies Cut");
        expect(fixture.nativeElement.querySelector("#title").textContent).toContain("Movies Cut");
    });
});
