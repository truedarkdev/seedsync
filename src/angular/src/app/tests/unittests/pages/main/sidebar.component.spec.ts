import {CommonModule} from "@angular/common";
import {ComponentFixture, TestBed} from "@angular/core/testing";
import {RouterTestingModule} from "@angular/router/testing";
import {BehaviorSubject, of} from "rxjs";

import {SidebarComponent} from "../../../../pages/main/sidebar.component";
import {LoggerService} from "../../../../services/utils/logger.service";
import {NotificationService} from "../../../../services/utils/notification.service";
import {ServerCommandService} from "../../../../services/server/server-command.service";
import {PathPair, PathPairService} from "../../../../services/settings/path-pair.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";


class MockPathPairService {
    private readonly _pathPairs = new BehaviorSubject<PathPair[]>([]);

    get pathPairs() {
        return this._pathPairs.asObservable();
    }

    setPathPairs(pathPairs: PathPair[]) {
        this._pathPairs.next(pathPairs);
    }
}

class MockLoggerService {
    info = jasmine.createSpy("info");
    debug = jasmine.createSpy("debug");
    error = jasmine.createSpy("error");
}

class MockServerCommandService {
    restart = jasmine.createSpy("restart").and.returnValue(
        of({success: true, data: "ok", errorMessage: null})
    );
}

class MockNotificationService {
    show = jasmine.createSpy("show");
    hide = jasmine.createSpy("hide");
}

class MockStreamServiceRegistry {
    connectedService = {
        connected: new BehaviorSubject(true).asObservable()
    };
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

describe("Testing sidebar component", () => {
    let fixture: ComponentFixture<SidebarComponent>;
    let component: SidebarComponent;
    let pathPairService: MockPathPairService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [SidebarComponent],
            imports: [
                CommonModule,
                RouterTestingModule.withRoutes([])
            ],
            providers: [
                {provide: LoggerService, useClass: MockLoggerService},
                {provide: ServerCommandService, useClass: MockServerCommandService},
                {provide: NotificationService, useClass: MockNotificationService},
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry},
                {provide: PathPairService, useClass: MockPathPairService}
            ]
        });

        fixture = TestBed.createComponent(SidebarComponent);
        component = fixture.componentInstance;
        pathPairService = TestBed.get(PathPairService);
    });

    afterEach(() => {
        fixture.destroy();
    });

    it("should show path pair tabs when multiple enabled path pairs exist", () => {
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies"),
            createPathPair("tv-id", "TV")
        ]);

        fixture.detectChanges();

        expect(component.hasMultipleEnabledPathPairs).toBe(true);
        expect(component.pathPairRoutes.length).toBe(2);
        expect(component.pathPairRoutes.map(route => route.path)).toEqual([
            "dashboard/movies",
            "dashboard/tv"
        ]);

        const buttons = Array.from(fixture.nativeElement.querySelectorAll("#sidebar a.button"));
        const labels = buttons.map((button: HTMLElement) => button.textContent.trim());
        expect(labels).toContain("Dashboard");
        expect(labels).toContain("Movies");
        expect(labels).toContain("TV");
    });

    it("should fall back to IDs when two enabled path pairs normalize to the same slug", () => {
        pathPairService.setPathPairs([
            createPathPair("movies-one", "My Movies"),
            createPathPair("movies-two", "My-Movies")
        ]);

        fixture.detectChanges();

        expect(component.hasMultipleEnabledPathPairs).toBe(true);
        expect(component.pathPairRoutes.map(route => route.path)).toEqual([
            "dashboard/movies-one",
            "dashboard/movies-two"
        ]);
    });

    it("should hide path pair tabs when only one enabled path pair exists", () => {
        pathPairService.setPathPairs([
            createPathPair("movies-id", "Movies")
        ]);

        fixture.detectChanges();

        expect(component.hasMultipleEnabledPathPairs).toBe(false);
        expect(component.pathPairRoutes.length).toBe(1);
        expect(component.pathPairRoutes[0].path).toBe("dashboard/movies");

        const buttons = Array.from(fixture.nativeElement.querySelectorAll("#sidebar a.button"));
        const labels = buttons.map((button: HTMLElement) => button.textContent.trim());
        expect(labels).toContain("Dashboard");
        expect(labels).not.toContain("Movies");
        expect(labels).not.toContain("TV");
    });
});
