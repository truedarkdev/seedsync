import {ChangeDetectionStrategy, Component} from "@angular/core";
import {ComponentFixture, TestBed, fakeAsync, flushMicrotasks, tick} from "@angular/core/testing";
import {FormsModule} from "@angular/forms";
import {By} from "@angular/platform-browser";
import {BehaviorSubject, of} from "rxjs";
import {Modal} from "../../../../services/utils/modal.service";

import {PathPairsComponent} from "../../../../pages/settings/path-pairs.component";
import {
    PathPair,
    PathPairService
} from "../../../../services/settings/path-pair.service";
import {NotificationService} from "../../../../services/utils/notification.service";
import {Notification} from "../../../../services/utils/notification";
import {ModalAccessibilityService} from "../../../../services/utils/modal-accessibility.service";


class MockPathPairService {
    private _pathPairs = new BehaviorSubject<PathPair[]>([]);

    get pathPairs() {
        return this._pathPairs.asObservable();
    }

    push(pathPairs: PathPair[]) {
        this._pathPairs.next(pathPairs);
    }

    create = jasmine.createSpy("create").and.returnValue(of({
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
    update = jasmine.createSpy("update").and.returnValue(of({
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
    delete = jasmine.createSpy("delete").and.returnValue(of(null));
    reorder = jasmine.createSpy("reorder").and.returnValue(of([]));
}

class MockNotificationService {
    show = jasmine.createSpy("show");
}

class MockDialogResult {
    constructor(private shouldConfirm: boolean) {}

    then(resolve: Function, reject: Function) {
        if (this.shouldConfirm) {
            resolve();
        } else if (reject != null) {
            reject();
        }

        return this;
    }
}

class MockDialogBuilder {
    constructor(private shouldConfirm: boolean) {}

    title() { return this; }
    okBtn() { return this; }
    okBtnClass() { return this; }
    cancelBtn() { return this; }
    cancelBtnClass() { return this; }
    isBlocking() { return this; }
    showClose() { return this; }
    body() { return this; }
    open() {
        return Promise.resolve({
            result: new MockDialogResult(this.shouldConfirm)
        });
    }
}

class MockModal {
    public shouldConfirm = true;

    confirm() {
        return new MockDialogBuilder(this.shouldConfirm);
    }
}

class MockModalAccessibilityService {
    enhance = jasmine.createSpy("enhance").and.callFake((dialogRefPromise: Promise<any>) => dialogRefPromise);
}

@Component({
    changeDetection: ChangeDetectionStrategy.OnPush,
    standalone: false,
    template: "<app-path-pairs></app-path-pairs>"
})
class PathPairsHostComponent {
}


describe("Testing path pairs component", () => {
    let component: PathPairsComponent;
    let fixture: ComponentFixture<PathPairsHostComponent>;
    let pathPairService: MockPathPairService;
    let notificationService: MockNotificationService;
    let modal: MockModal;
    let modalAccessibilityService: MockModalAccessibilityService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [
                PathPairsHostComponent,
                PathPairsComponent
            ],
            imports: [
                FormsModule
            ],
            providers: [
                {provide: PathPairService, useClass: MockPathPairService},
                {provide: NotificationService, useClass: MockNotificationService},
                {provide: Modal, useClass: MockModal},
                {provide: ModalAccessibilityService, useClass: MockModalAccessibilityService}
            ]
        });

        fixture = TestBed.createComponent(PathPairsHostComponent);
        pathPairService = TestBed.get(PathPairService);
        notificationService = TestBed.get(NotificationService);
        modal = TestBed.get(Modal);
        modalAccessibilityService = TestBed.get(ModalAccessibilityService);
        fixture.detectChanges();
        fixture.autoDetectChanges(true);
        component = fixture.debugElement.query(By.directive(PathPairsComponent)).componentInstance;
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

    it("should replace the empty state when path pairs refresh", fakeAsync(() => {
        expect(fixture.nativeElement.querySelector(".empty-state")).not.toBeNull();
        expect(fixture.nativeElement.querySelectorAll(".path-pair-item").length).toBe(0);

        pathPairService.push([
            {
                id: "movies",
                name: "Movies",
                remote_path: "/remote/movies",
                local_path: "/downloads/movies",
                enabled: true,
                auto_queue: true
            }
        ]);
        fixture.detectChanges();

        expect(fixture.nativeElement.querySelector(".empty-state")).toBeNull();
        const items = fixture.nativeElement.querySelectorAll(".path-pair-item");
        expect(items.length).toBe(1);
        expect(items[0].querySelector(".pair-name").textContent.trim()).toBe("Movies");
    }));

    it("should delete a path pair after confirmation", fakeAsync(() => {
        modal.shouldConfirm = true;

        component.delete({
            id: "movies",
            name: "Movies",
            remote_path: "/remote/movies",
            local_path: "/downloads/movies",
            enabled: true,
            auto_queue: true
        });

        flushMicrotasks();

        expect(modalAccessibilityService.enhance).toHaveBeenCalled();
        expect(pathPairService.delete).toHaveBeenCalledWith("movies");
    }));

    it("should not delete a path pair when confirmation is cancelled", fakeAsync(() => {
        modal.shouldConfirm = false;

        component.delete({
            id: "movies",
            name: "Movies",
            remote_path: "/remote/movies",
            local_path: "/downloads/movies",
            enabled: true,
            auto_queue: true
        });

        flushMicrotasks();

        expect(pathPairService.delete).not.toHaveBeenCalled();
    }));
});
