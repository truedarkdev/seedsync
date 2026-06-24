import {ComponentFixture, TestBed} from "@angular/core/testing";
import {FormsModule} from "@angular/forms";
import {BehaviorSubject, of} from "rxjs";

import * as Immutable from "immutable";

import {AutoQueuePageComponent} from "../../../../pages/autoqueue/autoqueue-page.component";
import {AutoQueueService} from "../../../../services/autoqueue/autoqueue.service";
import {AutoQueuePattern} from "../../../../services/autoqueue/autoqueue-pattern";
import {NotificationService} from "../../../../services/utils/notification.service";
import {ConfigService} from "../../../../services/settings/config.service";
import {ConnectedService} from "../../../../services/utils/connected.service";
import {StreamServiceRegistry} from "../../../../services/base/stream-service.registry";
import {Config} from "../../../../services/settings/config";
import {PathPair, PathPairService} from "../../../../services/settings/path-pair.service";
import {WebReaction} from "../../../../services/utils/rest.service";


class MockAutoQueueService {
    private _patterns = new BehaviorSubject(Immutable.List<AutoQueuePattern>([]));

    get patterns() {
        return this._patterns.asObservable();
    }

    add = jasmine.createSpy("add").and.returnValue(of(new WebReaction(true, "ok", null)));
    remove = jasmine.createSpy("remove").and.returnValue(of(new WebReaction(true, "ok", null)));
}

class MockNotificationService {
    show = jasmine.createSpy("show");
}

class MockConfigService {
    private _config = new BehaviorSubject<Config>(null);

    get config() {
        return this._config.asObservable();
    }

    push(config: Config) {
        this._config.next(config);
    }
}

class MockPathPairService {
    private _pathPairs = new BehaviorSubject<PathPair[]>([]);

    get pathPairs() {
        return this._pathPairs.asObservable();
    }

    push(pathPairs: PathPair[]) {
        this._pathPairs.next(pathPairs);
    }
}

class MockConnectedService {
    connected = new BehaviorSubject(false);
}

class MockStreamServiceRegistry {
    connectedService = TestBed.get(ConnectedService);
}

function createConfig(enabled: boolean, patternsOnly: boolean): Config {
    return new Config({
        autoqueue: {
            enabled: enabled,
            patterns_only: patternsOnly
        }
    });
}

describe("Testing autoqueue page component", () => {
    let component: AutoQueuePageComponent;
    let fixture: ComponentFixture<AutoQueuePageComponent>;
    let autoQueueService: MockAutoQueueService;
    let configService: MockConfigService;
    let pathPairService: MockPathPairService;
    let connectedService: MockConnectedService;

    beforeEach(() => {
        TestBed.configureTestingModule({
            declarations: [AutoQueuePageComponent],
            imports: [FormsModule],
            providers: [
                {provide: AutoQueueService, useClass: MockAutoQueueService},
                {provide: NotificationService, useClass: MockNotificationService},
                {provide: ConfigService, useClass: MockConfigService},
                {provide: PathPairService, useClass: MockPathPairService},
                {provide: ConnectedService, useClass: MockConnectedService},
                {provide: StreamServiceRegistry, useClass: MockStreamServiceRegistry}
            ]
        });

        fixture = TestBed.createComponent(AutoQueuePageComponent);
        component = fixture.componentInstance;
        autoQueueService = TestBed.get(AutoQueueService);
        configService = TestBed.get(ConfigService);
        pathPairService = TestBed.get(PathPairService);
        connectedService = TestBed.get(ConnectedService);

        fixture.detectChanges();
    });

    afterEach(() => {
        fixture.destroy();
    });

    it("should not add a pattern when editing is disabled", () => {
        connectedService.connected.next(false);
        configService.push(createConfig(true, true));
        component.newPattern = "show-*";

        component.onAddPattern();

        expect(autoQueueService.add).not.toHaveBeenCalled();
    });

    it("should add a pattern when editing is enabled", () => {
        connectedService.connected.next(true);
        configService.push(createConfig(true, true));
        component.newPattern = "show-*";

        component.onAddPattern();

        expect(autoQueueService.add).toHaveBeenCalledWith("show-*");
        expect(component.newPattern).toBe("");
    });

    it("should not remove a pattern when editing is disabled", () => {
        connectedService.connected.next(true);
        configService.push(createConfig(false, false));

        component.onRemovePattern(new AutoQueuePattern({pattern: "show-*"}));

        expect(autoQueueService.remove).not.toHaveBeenCalled();
    });

    it("should stay editable when path-pair autoqueue is active even if global autoqueue is disabled", () => {
        connectedService.connected.next(true);
        configService.push(createConfig(false, true));
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

        const description = fixture.nativeElement.querySelector("#description").textContent;
        const input = fixture.nativeElement.querySelector("input[type='search']") as HTMLInputElement;

        expect(component.enabled).toBe(true);
        expect(component.isPatternEditingEnabled()).toBe(true);
        expect(description).toContain("Files matching these patterns will be automatically queued.");
        expect(description).not.toContain("Auto-Queue is disabled.");
        expect(input.disabled).toBe(false);
    });

    it("should stay disabled when enabled path pairs all opt out even if global autoqueue is enabled", () => {
        connectedService.connected.next(true);
        configService.push(createConfig(true, true));
        pathPairService.push([
            {
                id: "movies",
                name: "Movies",
                remote_path: "/remote/movies",
                local_path: "/downloads/movies",
                enabled: true,
                auto_queue: false
            }
        ]);

        fixture.detectChanges();

        const description = fixture.nativeElement.querySelector("#description").textContent;
        const controls = fixture.nativeElement.querySelector("#controls");

        expect(component.enabled).toBe(false);
        expect(component.isPatternEditingEnabled()).toBe(false);
        expect(description).toContain("Enable AutoQueue on a path pair");
        expect(controls.classList.contains("disabled")).toBe(true);
    });
});
