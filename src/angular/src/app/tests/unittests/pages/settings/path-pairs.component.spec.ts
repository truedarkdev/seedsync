import {ComponentFixture, TestBed} from "@angular/core/testing";
import {FormsModule} from "@angular/forms";
import {Observable} from "rxjs/Observable";
import {BehaviorSubject} from "rxjs/Rx";
import "rxjs/add/observable/of";
import "rxjs/add/observable/throw";

import {PathPairsComponent} from "../../../../pages/settings/path-pairs.component";
import {
    PathPair,
    PathPairService
} from "../../../../services/settings/path-pair.service";
import {NotificationService} from "../../../../services/utils/notification.service";
import {Notification} from "../../../../services/utils/notification";


class MockPathPairService {
    private _pathPairs = new BehaviorSubject<PathPair[]>([]);

    get pathPairs() {
        return this._pathPairs.asObservable();
    }

    push(pathPairs: PathPair[]) {
        this._pathPairs.next(pathPairs);
    }

    create = jasmine.createSpy("create").and.returnValue(Observable.of({
        pathPair: {
            id: "movies",
            name: "Movies",
            remote_path: "/remote/movies",
            local_path: "/downloads/movies",
            enabled: true,
            auto_queue: true
        },
        warnings: ["warning"]
    }));
    update = jasmine.createSpy("update").and.returnValue(Observable.of({
        pathPair: {
            id: "movies",
            name: "Movies",
            remote_path: "/remote/movies",
            local_path: "/downloads/movies",
            enabled: true,
            auto_queue: true
        },
        warnings: []
    }));
    delete = jasmine.createSpy("delete").and.returnValue(Observable.of(null));
    reorder = jasmine.createSpy("reorder").and.returnValue(Observable.of([]));
}

class MockNotificationService {
    show = jasmine.createSpy("show");
}


describe("Testing path pairs component", () => {
    let component: PathPairsComponent;
    let fixture: ComponentFixture<PathPairsComponent>;
    let pathPairService: MockPathPairService;
    let notificationService: MockNotificationService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [
                PathPairsComponent
            ],
            imports: [
                FormsModule
            ],
            providers: [
                {provide: PathPairService, useClass: MockPathPairService},
                {provide: NotificationService, useClass: MockNotificationService}
            ]
        });

        fixture = TestBed.createComponent(PathPairsComponent);
        component = fixture.componentInstance;
        pathPairService = TestBed.get(PathPairService);
        notificationService = TestBed.get(NotificationService);
        fixture.detectChanges();
    });

    it("should create an instance", () => {
        expect(component).toBeDefined();
    });

    it("should create a path pair and show warnings", () => {
        component.startCreate();
        component.formRemotePath = "/remote/movies";
        component.formLocalPath = "/downloads/movies";
        component.save();

        expect(pathPairService.create).toHaveBeenCalledWith({
            name: "movies",
            remote_path: "/remote/movies",
            local_path: "/downloads/movies",
            enabled: true,
            auto_queue: true
        });
        expect(notificationService.show).toHaveBeenCalled();
    });

    it("should populate the form when editing", () => {
        const pair = {
            id: "movies",
            name: "Movies",
            remote_path: "/remote/movies",
            local_path: "/downloads/movies",
            enabled: false,
            auto_queue: false
        };

        component.startEdit(pair);

        expect(component.isEditing).toBe(true);
        expect(component.formName).toBe("Movies");
        expect(component.formEnabled).toBe(false);
        expect(component.formAutoQueue).toBe(false);
    });

    it("should reorder path pairs when moving up", () => {
        pathPairService.push([
            {
                id: "movies",
                name: "Movies",
                remote_path: "/remote/movies",
                local_path: "/downloads/movies",
                enabled: true,
                auto_queue: true
            },
            {
                id: "tv",
                name: "TV",
                remote_path: "/remote/tv",
                local_path: "/downloads/tv",
                enabled: true,
                auto_queue: true
            }
        ]);

        component.moveUp(1);

        expect(pathPairService.reorder).toHaveBeenCalledWith(["tv", "movies"]);
    });

    it("should delete a path pair after confirmation", () => {
        spyOn(window, "confirm").and.returnValue(true);

        component.delete({
            id: "movies",
            name: "Movies",
            remote_path: "/remote/movies",
            local_path: "/downloads/movies",
            enabled: true,
            auto_queue: true
        });

        expect(pathPairService.delete).toHaveBeenCalledWith("movies");
    });

    it("should not delete a path pair when confirmation is cancelled", () => {
        spyOn(window, "confirm").and.returnValue(false);

        component.delete({
            id: "movies",
            name: "Movies",
            remote_path: "/remote/movies",
            local_path: "/downloads/movies",
            enabled: true,
            auto_queue: true
        });

        expect(pathPairService.delete).not.toHaveBeenCalled();
    });
});
